"""Domain-specific tool profiles and context constants.

Tool categories compose into profiles for different agent use cases.
People context comes from the gitignored user_context module.
"""

try:
    from .user_context import KNOWN_PEOPLE, KNOWN_PEOPLE_ONELINER
except ImportError:
    KNOWN_PEOPLE = ""
    KNOWN_PEOPLE_ONELINER = ""

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
    "mcp__obsidian-pkm__vault_link_health",
]

_VAULT_WRITE = [
    "mcp__obsidian-pkm__vault_write",
    "mcp__obsidian-pkm__vault_append",
    "mcp__obsidian-pkm__vault_edit",
    "mcp__obsidian-pkm__vault_update_frontmatter",
    "mcp__obsidian-pkm__vault_add_links",
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

# Capture is strictly append-only: read-side + vault_append + vault_add_links.
# No vault_write, no vault_edit, no vault_update_frontmatter, no admin.
TOOLS_CAPTURE = _VAULT_READ + [
    "mcp__obsidian-pkm__vault_append",
    "mcp__obsidian-pkm__vault_add_links",
]
