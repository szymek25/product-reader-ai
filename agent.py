"""
product-reader-ai – Strands agent entry point.

Environment variables (see .env.example):
  GITHUB_TOKEN                  GitHub personal access token with repo + workflow scopes
  TARGET_REPO                   owner/repo of the repository to commit files to
  BASE_BRANCH                   branch to create the new branch from (default: main)
  WEBSHOP_URLS                  comma-separated list of webshop URLs to browse
  FEATURES_PATH                 path in TARGET_REPO with product feature/test examples
                                used to learn the profile schema (default: features/products)
  MOCKS_PATH                    path in TARGET_REPO with mock HTML pages used to learn
                                the test scenario structure (default: public/mock)
  PROFILES_PATH                 output path for generated profile files
                                (default: validation/profiles)
  TESTS_PATH                    output path for generated test scenario files
                                (default: validation/tests)
  GENERATE_BASELINE_WORKFLOW    workflow name for generating baseline artifacts
                                (default: Validation — Generate Baseline)
  ACCEPT_BASELINE_WORKFLOW      workflow name for accepting a baseline
                                (default: Validation — Accept Baseline)
  AWS_REGION                    AWS region for Bedrock (default: us-east-1)
  BEDROCK_MODEL_ID              Bedrock model ID (default: us.anthropic.claude-sonnet-4-5-v1:0)
  GITHUB_MCP_COMMAND            full command used to launch the GitHub MCP server
                                (space-separated, first token is the executable).
                                When set, overrides the default local binary.
                                Examples:
                                  docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN ghcr.io/github/github-mcp-server
                                  podman run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN ghcr.io/github/github-mcp-server
                                  /usr/local/bin/github-mcp-server stdio
                                If not set, the agent expects `github-mcp-server` on PATH.
  STRANDS_BROWSER_HEADLESS      set to "true" to run the browser in headless mode (default: true)
"""

import os

from dotenv import load_dotenv
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.models.bedrock import BedrockModel
from strands.tools.executors import SequentialToolExecutor
from strands.tools.mcp import MCPClient
from strands_tools.browser import LocalChromiumBrowser
from strands_tools.file_write import file_write

from prompts import SYSTEM_PROMPT, TASK_PROMPT_TEMPLATE
from product_page_agent import analyze_product_page
from product_links_agent import find_product_links
from state import (
    add_product,
    load_mismatch_log,
    load_products,
    load_run_state,
    load_schema,
    log_mismatch,
    lookup_slug,
    register_slug,
    save_products,
    save_run_state,
    save_schema,
)

load_dotenv()

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
TARGET_REPO = os.environ.get("TARGET_REPO", "")
BASE_BRANCH = os.environ.get("BASE_BRANCH", "main")
WEBSHOP_URLS_RAW = os.environ.get("WEBSHOP_URLS", "")
FEATURES_PATH = os.environ.get("FEATURES_PATH", "features/products")
MOCKS_PATH = os.environ.get("MOCKS_PATH", "public/mock")
PROFILES_PATH = os.environ.get("PROFILES_PATH", "validation/profiles")
TESTS_PATH = os.environ.get("TESTS_PATH", "validation/tests")
GENERATE_BASELINE_WORKFLOW = os.environ.get(
    "GENERATE_BASELINE_WORKFLOW", "Validation — Generate Baseline"
)
ACCEPT_BASELINE_WORKFLOW = os.environ.get(
    "ACCEPT_BASELINE_WORKFLOW", "Validation — Accept Baseline"
)
GITHUB_MCP_COMMAND = os.environ.get("GITHUB_MCP_COMMAND", "").strip()
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-v1:0"
)

# Run browser in headless mode by default for CI-friendliness
os.environ.setdefault("STRANDS_BROWSER_HEADLESS", "true")


def _validate_config() -> None:
    """Raise an informative error if required configuration is missing."""
    missing = []
    if not GITHUB_TOKEN:
        missing.append("GITHUB_TOKEN")
    if not TARGET_REPO:
        missing.append("TARGET_REPO")
    if not WEBSHOP_URLS_RAW:
        missing.append("WEBSHOP_URLS")
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Copy .env.example to .env and fill in the values."
        )


def _build_task_prompt() -> str:
    """Build the task prompt from environment variables."""
    webshop_urls = "\n".join(
        f"  - {url.strip()}"
        for url in WEBSHOP_URLS_RAW.split(",")
        if url.strip()
    )
    return TASK_PROMPT_TEMPLATE.format(
        webshop_urls=webshop_urls,
        target_repo=TARGET_REPO,
        base_branch=BASE_BRANCH,
        features_path=FEATURES_PATH,
        mocks_path=MOCKS_PATH,
        profiles_path=PROFILES_PATH,
        tests_path=TESTS_PATH,
        generate_baseline_workflow=GENERATE_BASELINE_WORKFLOW,
        accept_baseline_workflow=ACCEPT_BASELINE_WORKFLOW,
    )


def build_agent() -> tuple[LocalChromiumBrowser, BedrockModel, MCPClient]:
    """
    Construct the Strands agent with all required tools.

    Returns a tuple of (agent, github_mcp_client) so the caller can manage
    the MCP client context.
    """
    # ── Browser tool ─────────────────────────────
    browser = LocalChromiumBrowser()

    # ── GitHub MCP server (stdio transport) ─────
    if GITHUB_MCP_COMMAND:
        tokens = GITHUB_MCP_COMMAND.split()
        mcp_command, mcp_args = tokens[0], tokens[1:]
    else:
        mcp_command, mcp_args = "github-mcp-server", ["stdio"]
    github_mcp_params = StdioServerParameters(
        command=mcp_command,
        args=mcp_args,
        env={
            **os.environ,
            "GITHUB_PERSONAL_ACCESS_TOKEN": GITHUB_TOKEN,
            # Enable the actions toolset so workflow-trigger tools are available.
            "GITHUB_TOOLSETS": "default,actions",
        },
    )
    github_mcp_client = MCPClient(lambda: stdio_client(github_mcp_params))

    # ── Language model (Amazon Bedrock) ─────────
    model = BedrockModel(
        model_id=BEDROCK_MODEL_ID,
        region_name=AWS_REGION,
        # Cap per-response output to avoid bloating the conversation history.
        max_tokens=4096,
    )

    # NOTE: github_mcp_client.tools is only accessible after entering the
    # client's context manager.  We return the ingredients separately so
    # main() can assemble the Agent inside `with github_mcp_client:`.
    return browser, model, github_mcp_client


def main() -> None:
    """Entry point for the product-reader-ai agent."""
    _validate_config()

    browser, model, github_mcp_client = build_agent()

    # MCPClient is a ToolProvider — pass it directly to Agent.
    # SequentialToolExecutor prevents concurrent Playwright calls which cause
    # asyncio context conflicts when the LLM outputs multiple browser tool calls.
    # SlidingWindowConversationManager keeps history within the context window
    # and truncates oversized tool results (e.g. raw browser page content).
    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[
            browser.browser,
            file_write,
            github_mcp_client,
            # Product-page analysis sub-agent
            analyze_product_page,
            # Product-link discovery sub-agent
            find_product_links,
            # Local state persistence
            save_schema,
            load_schema,
            add_product,
            save_products,
            load_products,
            save_run_state,
            load_run_state,
            log_mismatch,
            load_mismatch_log,
            register_slug,
            lookup_slug,
        ],
        tool_executor=SequentialToolExecutor(),
        conversation_manager=SlidingWindowConversationManager(
            # 10 turns: browser payloads are very large; fewer messages in window
            # prevents "no valid trim point" overflow errors.
            window_size=10,
            should_truncate_results=True,
        ),
    )

    task = _build_task_prompt()
    print("=" * 60)
    print("Starting product-reader-ai agent …")
    print("=" * 60)
    result = agent(task)
    print("=" * 60)
    print("Agent completed.")
    print("=" * 60)
    print(result)


if __name__ == "__main__":
    main()
