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
  STRANDS_BROWSER_HEADLESS      set to "true" to run the browser in headless mode (default: true)
"""

import os

from dotenv import load_dotenv
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.tools.mcp import MCPClient
from strands_tools.browser import LocalChromiumBrowser
from strands_tools.file_write import file_write

from prompts import SYSTEM_PROMPT, TASK_PROMPT_TEMPLATE

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


def build_agent() -> tuple[Agent, MCPClient]:
    """
    Construct the Strands agent with all required tools.

    Returns a tuple of (agent, github_mcp_client) so the caller can manage
    the MCP client context.
    """
    # ── Browser tool ─────────────────────────────
    browser = LocalChromiumBrowser()

    # ── GitHub MCP server (stdio transport) ─────
    github_mcp_params = StdioServerParameters(
        command="github-mcp-server",
        args=["stdio"],
        env={**os.environ, "GITHUB_PERSONAL_ACCESS_TOKEN": GITHUB_TOKEN},
    )
    github_mcp_client = MCPClient(lambda: stdio_client(github_mcp_params))

    # ── Language model (Amazon Bedrock) ─────────
    model = BedrockModel(
        model_id=BEDROCK_MODEL_ID,
        region_name=AWS_REGION,
    )

    return (
        Agent(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            tools=[
                browser.browser,
                file_write,
            ],
        ),
        github_mcp_client,
    )


def main() -> None:
    """Entry point for the product-reader-ai agent."""
    _validate_config()

    agent, github_mcp_client = build_agent()

    with github_mcp_client:
        # Extend the agent's toolset with the GitHub MCP tools
        agent.tools = [*agent.tools, *github_mcp_client.tools]

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
