from __future__ import annotations
from datetime import datetime


def parse_date(recorded_at: str) -> datetime:
    """Parse an ISO 8601 date string, falling back to now() on failure."""
    try:
        return datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.now()


def extract_file_path(tool_name: str, tool_input: dict) -> str | None:
    """Extract PKM file path from vault_write/vault_append/vault_edit tool calls."""
    if tool_name in (
        "mcp__obsidian-pkm__vault_write",
        "mcp__obsidian-pkm__vault_append",
        "mcp__obsidian-pkm__vault_edit",
    ):
        return tool_input.get("path")
    return None
