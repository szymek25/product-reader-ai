"""
Specialized sub-agent for generating and committing the profile JSON (STEP 3).

Responsibility
──────────────
Read the shared schema, saved selectors, and collected products from disk, then
build a valid profile JSON file and commit it to the feature branch in the
target GitHub repository.

Artifact contract
─────────────────
IN  : slug (str), target_repo (str), branch (str), profiles_path (str)
      – all other data (schema, selectors, products) loaded from disk
OUT : committed file at  <profiles_path>/<slug>.json  on <branch>
      – return value is confirmation string with the commit SHA

A2A readiness
─────────────
The public entry point is the @tool `write_profile`.  All parameters are plain
strings so they can be passed over an A2A task card unchanged.
"""

from __future__ import annotations

import json

from strands import Agent, tool
from strands.agent.conversation_manager import SlidingWindowConversationManager

from model_factory import build_model, main_agent_model_id
import context
from state import load_products, load_schema, load_selectors


def _slim_selectors(raw: str) -> str:
    """Keep only role, selector, type — drop surrounding_html and sample_text."""
    try:
        return json.dumps(
            [{"role": s.get("role"), "selector": s.get("selector"), "type": s.get("type")}
             for s in json.loads(raw)],
            ensure_ascii=False,
        )
    except (json.JSONDecodeError, TypeError):
        return raw


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

PROFILE_WRITER_SYSTEM_PROMPT = """\
You are a profile-writing agent.

You receive:
  - A JSON schema describing the exact structure of a valid profile file.
  - A JSON array of CSS selectors (role → selector mapping).
  - A JSON array of 15 sample products collected from the webshop.

Your job:
1. Build a profile JSON file that conforms EXACTLY to the schema structure.
   Mirror all field names, types, and nesting — do not invent or omit fields.
2. Populate each selector field in the profile using the provided selectors.
3. Commit the file to the specified repository branch using create_or_update_file.
   Always pass the branch name explicitly.
4. Output ONLY the commit confirmation — no prose.
"""


def _build_profile_writer_agent(github_mcp_client) -> Agent:
    model = build_model(main_agent_model_id(), max_tokens=4096)
    return Agent(
        model=model,
        system_prompt=PROFILE_WRITER_SYSTEM_PROMPT,
        tools=[github_mcp_client],
        conversation_manager=SlidingWindowConversationManager(
            window_size=10, should_truncate_results=True
        ),
    )


@tool
def write_profile(
    slug: str,
    target_repo: str,
    branch: str,
    profiles_path: str,
) -> str:
    """
    Build and commit the product profile JSON for a webshop.

    Reads schema (shared), selectors, and products from disk; generates the
    profile JSON; commits it to *branch* in *target_repo*.

    Args:
        slug:             Webshop slug (e.g. ``"balticapets-pl"``).
        target_repo:      ``owner/repo`` to commit the file into.
        branch:           Feature branch to commit to (e.g. ``"feature/balticapets-pl"``).
        profiles_path:    Path inside the repo for profile files (e.g. ``"validation/profiles"``).

    Returns:
        Confirmation string including the committed file path.
    """
    schema = load_schema._tool_func()
    selectors = _slim_selectors(load_selectors._tool_func(slug))
    products = _slim_products(load_products._tool_func(slug))

    agent = _build_profile_writer_agent(context.github_mcp_client)
    prompt = (
        f"Webshop slug: {slug}\n"
        f"Target repository: {target_repo}\n"
        f"Branch: {branch}\n"
        f"Commit path: {profiles_path}/{slug}.json\n\n"
        f"=== SCHEMA ===\n{schema}\n\n"
        f"=== SELECTORS ===\n{selectors}\n\n"
        f"=== SAMPLE PRODUCTS (15) ===\n{products}\n\n"
        f"Build the profile JSON following the schema exactly, then commit it "
        f"to {profiles_path}/{slug}.json on branch {branch} in {target_repo}."
    )
    return str(agent(prompt))


if __name__ == "__main__":
    import argparse

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Run profile_writer_agent standalone."
    )
    parser.add_argument("slug", help="Webshop slug (e.g. balticapets-pl)")
    parser.add_argument("target_repo", help="owner/repo")
    parser.add_argument("branch", help="Feature branch name")
    parser.add_argument("--profiles-path", default="validation/profiles")
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
        result = write_profile._tool_func(
            slug=args.slug,
            target_repo=args.target_repo,
            branch=args.branch,
            profiles_path=args.profiles_path,
        )
    print(result)
