from __future__ import annotations
from datetime import datetime


def parse_date(recorded_at: str) -> datetime:
    """Parse an ISO 8601 date string, falling back to now() on failure."""
    try:
        return datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.now()


def extract_file_path(tool_name: str, tool_input: dict) -> str | None:
    """Extract PKM file path from vault_write/vault_append/vault_edit tool calls.

    These are the "primary write" tools whose paths populate
    `AgentLoopResult.files_written`. Auxiliary mutations
    (frontmatter updates, link additions) flow through
    `extract_aux_path` instead.
    """
    if tool_name in (
        "mcp__obsidian-pkm__vault_write",
        "mcp__obsidian-pkm__vault_append",
        "mcp__obsidian-pkm__vault_edit",
    ):
        return tool_input.get("path")
    return None


# Auxiliary path-touching tools. Tracked separately from `files_written` so
# dedup-skip decisions are auditable: when an extraction agent encounters a
# similarity-> 0.8 match, the per-item write protocol switches from vault_write
# to vault_update_frontmatter / vault_append / vault_add_links. Without
# distinguishing those calls, "agent claimed success but files_written has
# only the inbox summary" is indistinguishable between "correctly routed five
# items, four as updates" and "wrongly dropped four items as false-positive
# dedup hits."
_AUX_FRONTMATTER = "mcp__obsidian-pkm__vault_update_frontmatter"
_AUX_ADD_LINKS = "mcp__obsidian-pkm__vault_add_links"


def extract_aux_path(tool_name: str, tool_input: dict) -> tuple[str, str] | None:
    """Return ('frontmatter'|'links', path) for auxiliary vault mutations.

    Returns None for tools that aren't auxiliary mutations.
    """
    if tool_name == _AUX_FRONTMATTER:
        path = tool_input.get("path")
        return ("frontmatter", path) if path else None
    if tool_name == _AUX_ADD_LINKS:
        path = tool_input.get("path")
        return ("links", path) if path else None
    return None
