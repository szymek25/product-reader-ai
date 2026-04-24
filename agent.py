"""
product-reader-ai – Strands orchestrator agent entry point.

The orchestrator drives the high-level workflow (STEP 0–9).  All domain work
is delegated to specialised sub-agents exposed as @tool wrappers:

  schema_agent         – STEP 0: reads reference files, persists shared schema
  product_links_agent  – STEP 1a: discovers product URLs via HTTP
  product_page_agent   – STEP 1b: derives CSS selectors for a product page
  scraper_agent        – STEP 1c: extracts structured data from a product URL
  profile_writer_agent – STEP 3: builds + commits the profile JSON
  test_writer_agent    – STEP 4: builds + commits the test scenarios JSON
  validation_agent     – STEP 6–7: baseline dispatch → verify → retry loop

Environment variables (see .env.example):
  GITHUB_TOKEN                  GitHub personal access token with repo + workflow scopes
  TARGET_REPO                   owner/repo of the repository to commit files to
  BASE_BRANCH                   branch to create the new branch from (default: main)
  WEBSHOP_URLS                  comma-separated list of webshop URLs to process
  FEATURES_PATH                 path in TARGET_REPO with product feature/profile examples
  MOCKS_PATH                    path in TARGET_REPO with mock HTML / test scenario examples
  PROFILES_PATH                 output path for generated profile files
  TESTS_PATH                    output path for generated test scenario files
  GENERATE_BASELINE_WORKFLOW    workflow name for generating baseline artifacts
  ACCEPT_BASELINE_WORKFLOW      workflow name for accepting a baseline
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
"""

import os

from dotenv import load_dotenv
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.tools.executors import SequentialToolExecutor
from strands.tools.mcp import MCPClient

import context
from model_factory import build_model, main_agent_model_id
from prompts import SYSTEM_PROMPT, TASK_PROMPT_TEMPLATE
from product_page_agent import analyze_product_page
from product_links_agent import find_product_links
from scraper_agent import scrape_all_products
from profile_writer_agent import write_profile
from test_writer_agent import write_tests
from validation_agent import validate_baseline
from state import (
    load_mismatch_log,
    load_product_links,
    load_products,
    load_run_state,
    load_selectors,
    log_mismatch,
    register_slug,
    resolve_slug,
    save_product_links,
    save_products,
    save_run_state,
    save_selectors,
)

load_dotenv()

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
TARGET_REPO = os.environ.get("TARGET_REPO", "")
BASE_BRANCH = os.environ.get("BASE_BRANCH", "main")
WEBSHOP_URLS_RAW = os.environ.get("WEBSHOP_URLS", "")
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
        profiles_path=PROFILES_PATH,
        tests_path=TESTS_PATH,
        generate_baseline_workflow=GENERATE_BASELINE_WORKFLOW,
        accept_baseline_workflow=ACCEPT_BASELINE_WORKFLOW,
    )


def build_agent() -> MCPClient:
    """
    Construct the Strands agent ingredients.

    Returns the GitHub MCP client so the caller can manage the context.
    """
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

    # NOTE: github_mcp_client.tools is only accessible after entering the
    # client's context manager.  We return the client so main() can assemble
    # the Agent inside `with github_mcp_client:`.
    return github_mcp_client


def main() -> None:
    """Entry point for the product-reader-ai agent."""
    _validate_config()

    github_mcp_client = build_agent()
    context.github_mcp_client = github_mcp_client

    # MCPClient is a ToolProvider — pass it directly to Agent.
    # SequentialToolExecutor ensures tool calls are serialised.
    # SlidingWindowConversationManager keeps history within the context window
    # and truncates oversized tool results.
    model = build_model(main_agent_model_id(), max_tokens=8192)
    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[
            github_mcp_client,
            # STEP 1a – discover product URLs
            find_product_links,
            # STEP 1b – derive CSS selectors for a product page
            analyze_product_page,
            # STEP 1c – scrape all product URLs and persist records
            scrape_all_products,
            # STEP 3 – build + commit the profile JSON
            write_profile,
            # STEP 4 – build + commit the test scenarios JSON
            write_tests,
            # STEP 6-7 – baseline dispatch → verify → retry loop
            validate_baseline,
            # Local state persistence
            save_selectors,
            load_selectors,
            save_product_links,
            load_product_links,
            save_products,
            load_products,
            save_run_state,
            load_run_state,
            log_mismatch,
            load_mismatch_log,
            register_slug,
            resolve_slug,
        ],
        tool_executor=SequentialToolExecutor(),
        conversation_manager=SlidingWindowConversationManager(
            # Sub-agents absorb HTML payloads; main agent messages are compact.
            window_size=20,
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
