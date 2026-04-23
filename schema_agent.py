"""
Specialized sub-agent for learning the profile + test-scenario schema (STEP 0).

Responsibility
──────────────
Read the reference files from the target GitHub repository and produce a
compact, structured summary of:
  - profile_schema   – field names, types, nesting, and example values
  - test_scenario_structure – keys, conventions, and expected-value patterns

The schema is webshop-independent and is stored once at:
  <TMPDIR>/product-reader-ai/schema.json

Any subsequent run checks load_schema() first; if non-empty, STEP 0 is skipped.

Artifact contract
─────────────────
IN  : target_repo (str), base_branch (str), features_path (str), mocks_path (str)
      – passed as a natural-language prompt
OUT : schema.json written via save_schema()
      – return value is the JSON string written (for orchestrator confirmation)

A2A readiness
─────────────
The public entry point is the @tool `learn_schema`.  All parameters are plain
strings so they can be passed over an A2A task card unchanged.
"""

from __future__ import annotations

from strands import Agent, tool
from strands.agent.conversation_manager import SlidingWindowConversationManager

from model_factory import build_model, main_agent_model_id
import context
from state import STATE_ROOT, load_schema, save_schema

SCHEMA_AGENT_SYSTEM_PROMPT = """\
You are a schema-learning agent.

Your job: read reference files from a GitHub repository and produce a
structured JSON summary of the profile schema and test scenario structure
used by that repository.

Workflow:
1. List and read every file under the given features_path in the repository.
2. List and read every file under the given mocks_path in the repository.
3. Synthesise a single JSON object with two top-level keys:
     "profile_schema"           – field names, types, nesting, and a short
                                   example value for every field present in
                                   the reference profile files.
     "test_scenario_structure"  – keys, nesting, and value conventions from
                                   the mock/test files.
4. Output ONLY the raw JSON object — no prose, no markdown fences.
   The orchestrator will store it immediately.
"""


def _build_schema_agent(github_mcp_client) -> Agent:
    model = build_model(main_agent_model_id(), max_tokens=4096)
    return Agent(
        model=model,
        system_prompt=SCHEMA_AGENT_SYSTEM_PROMPT,
        tools=[github_mcp_client],
        conversation_manager=SlidingWindowConversationManager(
            window_size=10, should_truncate_results=True
        ),
    )


@tool
def learn_schema(
    target_repo: str,
    base_branch: str,
    features_path: str,
    mocks_path: str,
) -> str:
    """
    Learn and persist the shared profile + test-scenario schema.

    Reads reference files from *target_repo* and writes the synthesised schema
    to ``<TMPDIR>/product-reader-ai/schema.json``.  Skips the GitHub read if a
    schema is already stored.

    Args:
        target_repo:    ``owner/repo`` of the repository containing reference files.
        base_branch:    Branch to read files from (e.g. ``"main"``).
        features_path:  Path inside the repo with product feature/profile examples.
        mocks_path:     Path inside the repo with mock HTML / test scenario files.

    Returns:
        The schema JSON string (also persisted to disk).
    """
    if (STATE_ROOT / "schema.json").exists():
        return load_schema._tool_func()

    agent = _build_schema_agent(context.github_mcp_client)
    prompt = (
        f"Repository: {target_repo}\n"
        f"Branch: {base_branch}\n"
        f"Features path (profile examples): {features_path}\n"
        f"Mocks path (test scenario examples): {mocks_path}\n\n"
        "Read all files under both paths and output a single JSON object with "
        "keys 'profile_schema' and 'test_scenario_structure'."
    )
    result = str(agent(prompt))
    save_schema._tool_func(result)
    return result


if __name__ == "__main__":
    import argparse

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Run schema_agent standalone to learn the profile/test schema."
    )
    parser.add_argument("target_repo", help="owner/repo (e.g. szymek25/product-reader)")
    parser.add_argument("--branch", default="main", help="Base branch (default: main)")
    parser.add_argument("--features-path", default="features/products")
    parser.add_argument("--mocks-path", default="public/mock")
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
        schema = learn_schema._tool_func(
            target_repo=args.target_repo,
            base_branch=args.branch,
            features_path=args.features_path,
            mocks_path=args.mocks_path,
        )
    print(schema)
