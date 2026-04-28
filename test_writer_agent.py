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

import json

from strands import Agent, tool
from strands.agent.conversation_manager import SlidingWindowConversationManager

from model_factory import build_model, main_agent_model_id
import context
from schemas import TEST_SCHEMA
from state import load_products


def _slim_products(raw: str) -> str:
    """Truncate long description HTML; keep only first image URL."""
    try:
        slimmed = []
        for p in json.loads(raw):
            s = dict(p)
            if isinstance(s.get("description"), str) and len(s["description"]) > 300:
                s["description"] = s["description"][:300] + "…"
            if isinstance(s.get("image_urls"), list):
                s["image_urls"] = s["image_urls"][:1]
            slimmed.append(s)
        return json.dumps(slimmed, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        return raw

TEST_WRITER_SYSTEM_PROMPT = """\
You are a test-scenario writing agent.

You receive:
  - The test scenario JSON schema rules (field names, types, nesting, and allowed values).
  - A JSON array of sample products collected from the webshop.
  - The already-committed profile file for the webshop (contains attribute keys).

Your job:
1. Build a test scenario JSON file that conforms EXACTLY to the schema rules.
   One test entry per product. Always include: product_name (TEXT),
   short_description (TEXT), description (HTML), image_urls (LINK).
   Add attributes.<key> (TEXT) entries for each attribute the product has.
2. Read the committed profile to discover which attribute keys exist.
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
        conversation_manager=SlidingWindowConversationManager(
            window_size=10, should_truncate_results=True
        ),
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
    schema = TEST_SCHEMA
    products = _slim_products(load_products._tool_func(slug))

    agent = _build_test_writer_agent(context.github_mcp_client)
    prompt = (
        f"Webshop slug: {slug}\n"
        f"Target repository: {target_repo}\n"
        f"Branch: {branch}\n"
        f"Profile file path: {profiles_path}/{slug}.json\n"
        f"Commit path: {tests_path}/{slug}.json\n\n"
        f"=== SCHEMA ===\n{schema}\n\n"
        f"=== SAMPLE PRODUCTS (15) ===\n{products}\n\n"
        f"Read the committed profile file at {profiles_path}/{slug}.json "
        f"on branch {branch} in {target_repo} — it contains the selectors. "
        f"Then build the test scenarios JSON following the schema exactly and "
        f"commit it to {tests_path}/{slug}.json on branch {branch} in {target_repo}."
    )
    return str(agent(prompt))


# ── Local-flow variant ────────────────────────────────────────────────────────

LOCAL_TEST_WRITER_SYSTEM_PROMPT = """\
You are a test-scenario writing agent (local mode).

You receive:
  - The test scenario JSON schema rules (field names, types, nesting, and allowed values).
  - A JSON array of sample products collected from the webshop.
  - A local path to the already-written profile file (contains selector/attribute keys).
  - A local file path where you must save the result.

Your job:
1. Call read_file_from_disk(profile_path) to load the profile and discover attribute keys.
2. Build a test scenario JSON file that conforms EXACTLY to the schema rules.
   One test entry per product. Always include: product_name (TEXT),
   short_description (TEXT), description (HTML), image_urls (LINK).
   Add attributes.<key> (TEXT) entries for each attribute the product has.
3. Call write_file_to_disk(tests_path, content) to save the file locally.
4. Output ONLY the confirmation returned by write_file_to_disk — no prose.
"""


def _build_local_test_writer_agent() -> Agent:
    from state import read_file_from_disk, write_file_to_disk

    model = build_model(main_agent_model_id(), max_tokens=4096)
    return Agent(
        model=model,
        system_prompt=LOCAL_TEST_WRITER_SYSTEM_PROMPT,
        tools=[read_file_from_disk, write_file_to_disk],
        conversation_manager=SlidingWindowConversationManager(
            window_size=10, should_truncate_results=True
        ),
    )


@tool
def write_tests_local(slug: str) -> str:
    """
    Build the test scenarios JSON and write it to the slug state directory.

    Local-flow equivalent of ``write_tests`` — no GitHub access required.
    Reads schema and products from disk; reads the local profile JSON written
    by ``write_profile_local``; generates test scenarios; writes them to
    ``<TMPDIR>/product-reader-ai/<slug>/tests.json``.

    Args:
        slug: Webshop slug (e.g. ``"balticapets-pl"``).

    Returns:
        Confirmation string with the output file path and byte count.
    """
    from state import STATE_ROOT

    schema = TEST_SCHEMA
    products = _slim_products(load_products._tool_func(slug))
    profile_path = str(STATE_ROOT / slug / "profile.json")
    output_path = str(STATE_ROOT / slug / "tests.json")

    agent = _build_local_test_writer_agent()
    prompt = (
        f"Webshop slug: {slug}\n"
        f"Profile file path: {profile_path}\n"
        f"Output file path: {output_path}\n\n"
        f"=== SCHEMA ===\n{schema}\n\n"
        f"=== SAMPLE PRODUCTS (15) ===\n{products}\n\n"
        f'Call read_file_from_disk("{profile_path}") to load the profile and '
        f"discover attribute keys. Then build the test scenarios JSON following "
        f"the schema exactly and call "
        f'write_file_to_disk("{output_path}", <json_content>).'
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
