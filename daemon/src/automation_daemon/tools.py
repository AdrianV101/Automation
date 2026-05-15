"""Domain-specific tool profiles for daemon-spawned agents.

The daemon runs under `permission_mode="bypassPermissions"`, so the tool
allowlist passed to the SDK is the safety boundary -- not the user. Two
invariants matter:

1. Every "vault" tool name carries the `mcp__obsidian-pkm__` prefix. Without
   the prefix, the SDK silently doesn't match the tool, and the rejection
   manifests at runtime as "agent attempted disallowed tool" (now visible
   via AgentLoopResult.tool_errors thanks to the C1 fix). The Enum below
   makes the prefix invariant impossible to violate at definition time.

2. Profiles are composable but immutable -- a runaway `.append(...)` or
   accidental aliasing must not silently widen the safety boundary. The
   profiles are exported as `frozenset` so mutation requires an explicit
   intent at the call site.
"""
from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

VAULT_PREFIX = "mcp__obsidian-pkm__"


class VaultTool(str, Enum):
    """Every MCP-prefixed Obsidian vault tool the daemon may invoke.

    Subclasses `str` so members compare equal to and hash identically to the
    underlying tool-name string. This means `"mcp__obsidian-pkm__vault_read"
    in TOOLS_EXTRACTION` works without explicit conversion, and tests can
    assert membership using either the enum or the literal string.
    """
    # Read-side
    READ = "mcp__obsidian-pkm__vault_read"
    PEEK = "mcp__obsidian-pkm__vault_peek"
    SEARCH = "mcp__obsidian-pkm__vault_search"
    LIST = "mcp__obsidian-pkm__vault_list"
    RECENT = "mcp__obsidian-pkm__vault_recent"
    LINKS = "mcp__obsidian-pkm__vault_links"
    NEIGHBORHOOD = "mcp__obsidian-pkm__vault_neighborhood"
    QUERY = "mcp__obsidian-pkm__vault_query"
    TAGS = "mcp__obsidian-pkm__vault_tags"
    ACTIVITY = "mcp__obsidian-pkm__vault_activity"
    SEMANTIC_SEARCH = "mcp__obsidian-pkm__vault_semantic_search"
    SUGGEST_LINKS = "mcp__obsidian-pkm__vault_suggest_links"
    LINK_HEALTH = "mcp__obsidian-pkm__vault_link_health"
    # Write-side
    WRITE = "mcp__obsidian-pkm__vault_write"
    APPEND = "mcp__obsidian-pkm__vault_append"
    EDIT = "mcp__obsidian-pkm__vault_edit"
    UPDATE_FRONTMATTER = "mcp__obsidian-pkm__vault_update_frontmatter"
    ADD_LINKS = "mcp__obsidian-pkm__vault_add_links"
    # Admin
    TRASH = "mcp__obsidian-pkm__vault_trash"
    MOVE = "mcp__obsidian-pkm__vault_move"


# Module-load self-check. If a future contributor adds a member without the
# prefix (e.g. a typo'd "mcp__obsidian-okm__..."), the daemon refuses to
# import rather than silently shipping a broken safety boundary.
assert all(t.value.startswith(VAULT_PREFIX) for t in VaultTool), (
    f"VaultTool members must all start with {VAULT_PREFIX!r}"
)


# -- Tool categories (composable, immutable) --

_VAULT_READ: frozenset[str] = frozenset({
    VaultTool.READ, VaultTool.PEEK, VaultTool.SEARCH, VaultTool.LIST,
    VaultTool.RECENT, VaultTool.LINKS, VaultTool.NEIGHBORHOOD, VaultTool.QUERY,
    VaultTool.TAGS, VaultTool.ACTIVITY, VaultTool.SEMANTIC_SEARCH,
    VaultTool.SUGGEST_LINKS, VaultTool.LINK_HEALTH,
})

_VAULT_WRITE: frozenset[str] = frozenset({
    VaultTool.WRITE, VaultTool.APPEND, VaultTool.EDIT,
    VaultTool.UPDATE_FRONTMATTER, VaultTool.ADD_LINKS,
})

_VAULT_ADMIN: frozenset[str] = frozenset({VaultTool.TRASH, VaultTool.MOVE})

_CODEBASE_READ: frozenset[str] = frozenset({"Read", "Glob", "Grep"})

_WEB: frozenset[str] = frozenset({"WebSearch", "WebFetch"})


# -- Composed tool profiles (immutable; SDK callers convert via as_list) --

TOOLS_EXTRACTION: frozenset[str] = _VAULT_READ | _VAULT_WRITE | _VAULT_ADMIN | _CODEBASE_READ
TOOLS_ASK: frozenset[str] = _VAULT_READ | _CODEBASE_READ | _WEB
TOOLS_COMMAND: frozenset[str] = _VAULT_READ | _VAULT_WRITE | _CODEBASE_READ | _WEB
TOOLS_TASK: frozenset[str] = _VAULT_READ | _VAULT_WRITE | _CODEBASE_READ | _WEB

# Capture is strictly append-only: read-side + vault_append + vault_add_links.
# Composed by set algebra so the negative invariant (no vault_write, no
# vault_edit, no vault_update_frontmatter, no admin, no codebase, no web) is
# expressible as `TOOLS_CAPTURE.isdisjoint(TOOLS_CAPTURE_FORBIDDEN)` -- see
# test_capture.py::TestToolsCaptureSubset.
TOOLS_CAPTURE: frozenset[str] = _VAULT_READ | frozenset({
    VaultTool.APPEND, VaultTool.ADD_LINKS,
})


def as_list(tools: Iterable[str]) -> list[str]:
    """Convert a tool profile to the list[str] the SDK options accept.

    The SDK's ClaudeAgentOptions.allowed_tools is typed as list[str]; the
    profiles above are frozensets for immutability. This helper is the
    single point of conversion and exists so call sites stay declarative.
    """
    return list(tools)
