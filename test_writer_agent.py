"""
Specialized sub-agent for generating and committing the test scenarios JSON (STEP 4).

Responsibility
──────────────
Read the shared schema, saved selectors, collected products, and the already-
committed profile file, then build a valid test scenarios JSON file and commit
it to the feature branch.

Artifact contract
─────────────────
IN  : slug (str), target_repo (str), branch (str),
      profiles_path (str), tests_path (str)
      – schema, selectors, products loaded from disk
      – committed profile file read from GitHub
OUT : committed file at  <tests_path>/<slug>.json  on <branch>
      – return value is confirmation string with the commit SHA

A2A readiness
─────────────
The public entry point is the @tool `write_tests`.  All parameters are plain
strings so they can be passed over an A2A task card unchanged.
"""

from __future__ import annotations

from strands import Agent, tool

from model_factory import build_model, main_agent_model_id
import context
from state import load_products, load_schema, load_selectors

TEST_WRITER_SYSTEM_PROMPT = """\
You are a test-scenario writing agent.

You receive:
  - A JSON schema describing the exact structure of a valid test scenario file.
  - A JSON array of CSS selectors (role → selector mapping).
  - A JSON array of 15 sample products collected from the webshop.
  - The already-committed profile file for the webshop.

Your job:
1. Build a test scenario JSON file that conforms EXACTLY to the schema structure.
   Mirror all field names, types, and nesting — do not invent or omit fields.
2. For each test scenario, use the product data as the expected values and the
   selectors as the extraction mechanism, following the conventions in the schema.
3. Commit the file to the specified repository branch using create_or_update_file.
   Always pass the branch name explicitly.
4. Output ONLY the commit confirmation — no prose.
"""


def _build_test_writer_agent(github_mcp_client) -> Agent:
    model = build_model(main_agent_model_id(), max_tokens=4096)
    return Agent(
        model=model,
        system_prompt=TEST_WRITER_SYSTEM_PROMPT,
        tools=[github_mcp_client],
    )


@tool
def write_tests(
    slug: str,
    target_repo: str,
    branch: str,
    profiles_path: str,
    tests_path: str,
) -> str:
    """
    Build and commit the test scenarios JSON for a webshop.

    Reads schema (shared), selectors, products, and the committed profile file;
    generates the test scenarios JSON; commits it to *branch* in *target_repo*.

    Args:
        slug:             Webshop slug (e.g. ``"balticapets-pl"``).
        target_repo:      ``owner/repo`` to commit the file into.
        branch:           Feature branch to commit to (e.g. ``"feature/balticapets-pl"``).
        profiles_path:    Path inside the repo for profile files (e.g. ``"validation/profiles"``).
        tests_path:       Path inside the repo for test scenario files (e.g. ``"validation/tests"``).

    Returns:
        Confirmation string including the committed file path.
    """
    schema = load_schema._tool_func()
    selectors = load_selectors._tool_func(slug)
    products = load_products._tool_func(slug)

    agent = _build_test_writer_agent(context.github_mcp_client)
    prompt = (
        f"Webshop slug: {slug}\n"
        f"Target repository: {target_repo}\n"
        f"Branch: {branch}\n"
        f"Profile file path: {profiles_path}/{slug}.json\n"
        f"Commit path: {tests_path}/{slug}.json\n\n"
        f"=== SCHEMA ===\n{schema}\n\n"
        f"=== SELECTORS ===\n{selectors}\n\n"
        f"=== SAMPLE PRODUCTS (15) ===\n{products}\n\n"
        f"First read the committed profile file at {profiles_path}/{slug}.json "
        f"on branch {branch} in {target_repo} to understand its structure. "
        f"Then build the test scenarios JSON following the schema exactly and "
        f"commit it to {tests_path}/{slug}.json on branch {branch} in {target_repo}."
    )
    return str(agent(prompt))


if __name__ == "__main__":
    import argparse

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Run test_writer_agent standalone."
    )
    parser.add_argument("slug", help="Webshop slug (e.g. balticapets-pl)")
    parser.add_argument("target_repo", help="owner/repo")
    parser.add_argument("branch", help="Feature branch name")
    parser.add_argument("--profiles-path", default="validation/profiles")
    parser.add_argument("--tests-path", default="validation/tests")
    args = parser.parse_args()

    import os

    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client
    from strands.tools.mcp import MCPClient

    GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
    GITHUB_MCP_COMMAND = os.environ.get("GITHUB_MCP_COMMAND", "").strip()
    if GITHUB_MCP_COMMAND:
        tokens = GITHUB_MCP_COMMAND.split()
        cmd, cmd_args = tokens[0], tokens[1:]
    else:
        cmd, cmd_args = "github-mcp-server", ["stdio"]

    params = StdioServerParameters(
        command=cmd,
        args=cmd_args,
        env={**os.environ, "GITHUB_PERSONAL_ACCESS_TOKEN": GITHUB_TOKEN},
    )
    client = MCPClient(lambda: stdio_client(params))

    with client:
        context.github_mcp_client = client
        result = write_tests._tool_func(
            slug=args.slug,
            target_repo=args.target_repo,
            branch=args.branch,
            profiles_path=args.profiles_path,
            tests_path=args.tests_path,
        )
    print(result)
