"""
Specialized sub-agent for baseline validation (STEP 6–7).

Responsibility
──────────────
1. Dispatch the Generate Baseline GitHub Actions workflow.
2. Poll until it reaches a terminal status.
3. Download the baseline artifact and compare every `preview` field against
   the products collected in STEP 1.
4. If mismatches are found: log them, fix the profile, commit, and retry
   (up to 3 attempts).
5. If all previews match: dispatch the Accept Baseline workflow and confirm.

Artifact contract
─────────────────
IN  : slug (str), target_repo (str), branch (str),
      profiles_path (str),
      generate_baseline_workflow (str), accept_baseline_workflow (str)
      – products.json loaded from disk for comparison
OUT : run_state.json updated with outcome and attempt count
      – return value is a status string ("passed" | "failed_after_3_attempts")

A2A readiness
─────────────
The public entry point is the @tool `validate_baseline`.  All parameters are
plain strings so they can be passed over an A2A task card unchanged.
"""

from __future__ import annotations

from strands import Agent, tool

from model_factory import build_model, main_agent_model_id
import context
from state import load_mismatch_log, load_products, load_run_state, log_mismatch, save_run_state

VALIDATION_AGENT_SYSTEM_PROMPT = """\
You are a baseline validation agent.

Your job: trigger a GitHub Actions workflow, wait for it to finish, verify the
produced baseline artifacts against expected product data, and either accept the
baseline or fix the profile and retry.

Workflow:
1. Dispatch the Generate Baseline workflow via actions_run_trigger with
   profile_id=<slug> on branch <branch>.
2. Poll actions_list (highest run_number on the same branch) every 20 seconds
   until status is one of: completed, failure, cancelled, timed_out,
   action_required.
3. Call actions_get(run_id) for full details.
4. Download the baseline artifact (look for a file containing "preview" fields).
5. For each product in the baseline artifact, compare every "preview" field
   against the corresponding value in the provided products JSON.
   - All match → dispatch the Accept Baseline workflow and return "passed".
   - Any mismatch → call log_mismatch for each differing field, then fix the
     profile by reading and updating the profile file on the branch, commit the
     fix, and go back to step 1.
   - After 3 total attempts → return "failed_after_3_attempts" without retrying.
6. Never re-run a previous workflow run — always dispatch a new one.
7. Output ONLY the final status string.
"""


def _build_validation_agent(github_mcp_client) -> Agent:
    model = build_model(main_agent_model_id(), max_tokens=4096)
    return Agent(
        model=model,
        system_prompt=VALIDATION_AGENT_SYSTEM_PROMPT,
        tools=[
            github_mcp_client,
            log_mismatch,
            load_mismatch_log,
            save_run_state,
            load_run_state,
        ],
    )


@tool
def validate_baseline(
    slug: str,
    target_repo: str,
    branch: str,
    profiles_path: str,
    generate_baseline_workflow: str,
    accept_baseline_workflow: str,
) -> str:
    """
    Run the baseline validation loop for a webshop profile.

    Dispatches the Generate Baseline workflow, polls until done, verifies
    preview values, retries with profile fixes if needed (≤ 3 attempts), and
    finally dispatches Accept Baseline on success.

    Args:
        slug:                        Webshop slug (e.g. ``"balticapets-pl"``).
        target_repo:                 ``owner/repo`` containing the workflows.
        branch:                      Feature branch the profile was committed to.
        profiles_path:               Path inside the repo for profile files.
        generate_baseline_workflow:  Name of the Generate Baseline workflow.
        accept_baseline_workflow:    Name of the Accept Baseline workflow.

    Returns:
        ``"passed"`` if the baseline was accepted, or
        ``"failed_after_3_attempts"`` if all retries were exhausted.
    """
    products = load_products._tool_func(slug)
    run_state = load_run_state._tool_func(slug)

    agent = _build_validation_agent(context.github_mcp_client)
    prompt = (
        f"Webshop slug: {slug}\n"
        f"Target repository: {target_repo}\n"
        f"Feature branch: {branch}\n"
        f"Profile file: {profiles_path}/{slug}.json\n"
        f"Generate Baseline workflow: {generate_baseline_workflow}\n"
        f"Accept Baseline workflow: {accept_baseline_workflow}\n\n"
        f"=== COLLECTED PRODUCTS (expected values) ===\n{products}\n\n"
        f"=== CURRENT RUN STATE ===\n{run_state}\n\n"
        "Run the baseline validation loop as described in your system prompt. "
        "Use save_run_state to record progress after each attempt."
    )
    return str(agent(prompt))


if __name__ == "__main__":
    import argparse

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Run validation_agent standalone."
    )
    parser.add_argument("slug", help="Webshop slug (e.g. balticapets-pl)")
    parser.add_argument("target_repo", help="owner/repo")
    parser.add_argument("branch", help="Feature branch name")
    parser.add_argument("--profiles-path", default="validation/profiles")
    parser.add_argument(
        "--generate-workflow", default="Validation — Generate Baseline"
    )
    parser.add_argument(
        "--accept-workflow", default="Validation — Accept Baseline"
    )
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
        env={
            **os.environ,
            "GITHUB_PERSONAL_ACCESS_TOKEN": GITHUB_TOKEN,
            "GITHUB_TOOLSETS": "default,actions",
        },
    )
    client = MCPClient(lambda: stdio_client(params))

    with client:
        context.github_mcp_client = client
        result = validate_baseline._tool_func(
            slug=args.slug,
            target_repo=args.target_repo,
            branch=args.branch,
            profiles_path=args.profiles_path,
            generate_baseline_workflow=args.generate_workflow,
            accept_baseline_workflow=args.accept_workflow,
        )
    print(result)
