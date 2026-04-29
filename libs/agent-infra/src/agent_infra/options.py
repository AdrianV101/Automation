from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions

log = logging.getLogger(__name__)

DISALLOWED_NATIVE_TOOLS = [
    "Edit", "Write", "Bash", "TodoWrite", "Task", "NotebookEdit", "Skill",
]


class AgentInfraConfigError(RuntimeError):
    """Raised when daemon config is missing/invalid (env vars, executables).

    Distinct from runtime errors so callers can re-raise these to the
    daemon-wide error handler instead of swallowing them as per-job
    failures: an OBSIDIAN_MCP_SERVER_PATH miss or absent `node` will fail
    every job until the operator fixes the host, not just this one.
    """


def build_agent_options(
    system_prompt: str, pkm_vault_path: Path,
    *, model: str = "claude-opus-4-6",
    allowed_tools: list[str] | None = None,
    mcp_server_path: str | None = None,
    max_turns: int | None = None,
) -> ClaudeAgentOptions:
    """Build standard ClaudeAgentOptions with Obsidian MCP config."""
    server_path = mcp_server_path or os.environ.get("OBSIDIAN_MCP_SERVER_PATH")
    if not server_path:
        raise AgentInfraConfigError(
            "OBSIDIAN_MCP_SERVER_PATH environment variable is not set. "
            "Set it to the path of your Obsidian MCP server's index.js."
        )
    node_path = shutil.which("node")
    if not node_path:
        raise AgentInfraConfigError(
            "node executable not found on PATH. The obsidian-pkm MCP server "
            "requires node; install it or extend the daemon's PATH "
            f"(current PATH={os.environ.get('PATH', '')!r})."
        )
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        max_turns=max_turns,
        mcp_servers={
            "obsidian-pkm": {
                "type": "stdio",
                "command": node_path,
                "args": [server_path],
                "env": {
                    "VAULT_PATH": str(pkm_vault_path),
                    **({"OPENAI_API_KEY": key} if (key := os.environ.get("OPENAI_API_KEY")) else {}),
                },
            },
        },
        allowed_tools=allowed_tools if allowed_tools is not None else [],
        disallowed_tools=DISALLOWED_NATIVE_TOOLS,
        permission_mode="bypassPermissions",
        model=model,
        env={"ANTHROPIC_API_KEY": ""},
    )
