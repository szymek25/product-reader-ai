"""
Shared runtime context for sub-agents.

Set ``github_mcp_client`` once in agent.py (inside the MCPClient context
manager) before running the orchestrator.  Sub-agents read it from here so
the value never has to be passed as a @tool parameter (which would expose
it to the LLM as a string argument it must supply).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from strands.tools.mcp import MCPClient

# Set by agent.py before the orchestrator runs.
github_mcp_client: "MCPClient | None" = None
