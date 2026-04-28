"""Domain-specific tool profiles.

Tool categories compose into profiles for different agent use cases.
"""

# -- Tool categories (composable) --

_VAULT_READ = [
    "mcp__obsidian-pkm__vault_read",
    "mcp__obsidian-pkm__vault_peek",
    "mcp__obsidian-pkm__vault_search",
    "mcp__obsidian-pkm__vault_list",
    "mcp__obsidian-pkm__vault_recent",
    "mcp__obsidian-pkm__vault_links",
    "mcp__obsidian-pkm__vault_neighborhood",
    "mcp__obsidian-pkm__vault_query",
    "mcp__obsidian-pkm__vault_tags",
    "mcp__obsidian-pkm__vault_activity",
    "mcp__obsidian-pkm__vault_semantic_search",
    "mcp__obsidian-pkm__vault_suggest_links",
]

_VAULT_WRITE = [
    "mcp__obsidian-pkm__vault_write",
    "mcp__obsidian-pkm__vault_append",
    "mcp__obsidian-pkm__vault_edit",
    "mcp__obsidian-pkm__vault_update_frontmatter",
]

_VAULT_ADMIN = [
    "mcp__obsidian-pkm__vault_trash",
    "mcp__obsidian-pkm__vault_move",
]

_CODEBASE_READ = ["Read", "Glob", "Grep"]

_WEB = ["WebSearch", "WebFetch"]

# -- Composed tool profiles --

TOOLS_EXTRACTION = _VAULT_READ + _VAULT_WRITE + _VAULT_ADMIN + _CODEBASE_READ
TOOLS_ASK = _VAULT_READ + _CODEBASE_READ + _WEB
TOOLS_COMMAND = _VAULT_READ + _VAULT_WRITE + _CODEBASE_READ + _WEB
TOOLS_TASK = _VAULT_READ + _VAULT_WRITE + _CODEBASE_READ + _WEB
