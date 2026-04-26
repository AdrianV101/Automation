"""Phase 4 guards: vault-pkm dogfood discipline in Telegram-command prompts.

Mirrors the structure of test_extraction.py::TestSystemPromptContents but targets
the four interactive prompts (NOTE/TASK/ASK/CHAT) plus a regression smoke check
on EXTRACTION_SYSTEM_PROMPT.
"""
import re

from audio_ingest.prompts import (
    ASK_SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT,
    EXTRACTION_SYSTEM_PROMPT,
    NOTE_SYSTEM_PROMPT,
    TASK_SYSTEM_PROMPT,
)
from audio_ingest.tools import TOOLS_ASK, TOOLS_TASK


VAULT_TOOL_RE = re.compile(r"\bvault_[a-z]+(?:_[a-z]+)*\b")


def _allowlisted_vault_tools(profile: list[str]) -> set[str]:
    """Strip the mcp__obsidian-pkm__ prefix to compare against prompt mentions."""
    prefix = "mcp__obsidian-pkm__"
    return {name[len(prefix):] for name in profile if name.startswith(prefix)}


class TestNoteSystemPrompt:
    def test_invokes_post_write_link_discovery(self) -> None:
        """After writing the note, the agent must run vault_suggest_links and vault_add_links."""
        assert "vault_suggest_links" in NOTE_SYSTEM_PROMPT
        assert "vault_add_links" in NOTE_SYSTEM_PROMPT

    def test_fleeting_note_template_is_explicit(self) -> None:
        """The fleeting-note template name must be visible to the agent.

        Asserts against the literal `template 'fleeting-note'` string rather than
        the bare template name, so a reviewer mentioning 'fleeting-note' in prose
        without the `vault_write with template 'fleeting-note'` instruction would
        fail this guard.
        """
        assert "template 'fleeting-note'" in NOTE_SYSTEM_PROMPT

    def test_keeps_filing_clerk_framing(self) -> None:
        """Phase 4 must preserve the CRITICAL CONSTRAINTS / filing clerk language."""
        assert "CRITICAL CONSTRAINTS" in NOTE_SYSTEM_PROMPT
        assert "filing clerk" in NOTE_SYSTEM_PROMPT

    def test_keeps_single_line_response_format(self) -> None:
        """The SINGLE-LINE response format must be preserved."""
        assert "SINGLE LINE" in NOTE_SYSTEM_PROMPT

    def test_only_references_allowlisted_tools(self) -> None:
        """Every vault_* token in the prompt must be in TOOLS_TASK (the /note profile)."""
        referenced = set(VAULT_TOOL_RE.findall(NOTE_SYSTEM_PROMPT))
        assert referenced, "expected NOTE_SYSTEM_PROMPT to reference at least one vault_* tool"
        allowed = _allowlisted_vault_tools(TOOLS_TASK)
        for name in referenced:
            assert name in allowed, (
                f"NOTE_SYSTEM_PROMPT references {name!r} but it is not in TOOLS_TASK"
            )

    def test_allowlist_includes_load_bearing_tools(self) -> None:
        """The vault-pkm tools the prompt's recipe relies on must remain allowlisted.

        The `*_only_references_allowlisted_tools` test is one-directional
        (prompt-mentioned subset of allowlist). It would still pass if a future
        edit dropped vault_add_links from BOTH the prompt and the allowlist --
        silently losing the linking capability. This positive assertion closes
        that gap by hard-pinning the load-bearing tools.
        """
        required = [
            "mcp__obsidian-pkm__vault_write",
            "mcp__obsidian-pkm__vault_append",
            "mcp__obsidian-pkm__vault_suggest_links",
            "mcp__obsidian-pkm__vault_add_links",
            "mcp__obsidian-pkm__vault_semantic_search",
        ]
        for name in required:
            assert name in TOOLS_TASK, f"{name} missing from TOOLS_TASK"


class TestTaskSystemPrompt:
    def test_invokes_post_write_link_discovery(self) -> None:
        assert "vault_suggest_links" in TASK_SYSTEM_PROMPT
        assert "vault_add_links" in TASK_SYSTEM_PROMPT

    def test_references_task_template(self) -> None:
        """Assert against the literal template-name string, not the bare word
        'task' (which appears in prose as 'task CAPTURE agent', 'task note', etc.
        and would make this guard trivially true).
        """
        assert "template 'task'" in TASK_SYSTEM_PROMPT

    def test_lists_status_enum(self) -> None:
        for keyword in ("pending", "active", "done", "cancelled"):
            assert keyword in TASK_SYSTEM_PROMPT, f"missing status enum value: {keyword}"

    def test_keeps_critical_constraints_and_response_format(self) -> None:
        assert "CRITICAL CONSTRAINTS" in TASK_SYSTEM_PROMPT
        assert "SINGLE LINE" in TASK_SYSTEM_PROMPT

    def test_only_references_allowlisted_tools(self) -> None:
        referenced = set(VAULT_TOOL_RE.findall(TASK_SYSTEM_PROMPT))
        assert referenced, "expected TASK_SYSTEM_PROMPT to reference at least one vault_* tool"
        allowed = _allowlisted_vault_tools(TOOLS_TASK)
        for name in referenced:
            assert name in allowed, (
                f"TASK_SYSTEM_PROMPT references {name!r} but it is not in TOOLS_TASK"
            )

    def test_allowlist_includes_load_bearing_tools(self) -> None:
        """The vault-pkm tools the prompt's recipe relies on must remain allowlisted.

        Catches a future edit that removes a load-bearing tool from BOTH the
        prompt body and the allowlist (which the subset test would not).
        """
        required = [
            "mcp__obsidian-pkm__vault_write",
            "mcp__obsidian-pkm__vault_append",
            "mcp__obsidian-pkm__vault_suggest_links",
            "mcp__obsidian-pkm__vault_add_links",
            "mcp__obsidian-pkm__vault_semantic_search",
        ]
        for name in required:
            assert name in TOOLS_TASK, f"{name} missing from TOOLS_TASK"


class TestAskSystemPrompt:
    def test_keeps_semantic_search_primary(self) -> None:
        assert "vault_semantic_search" in ASK_SYSTEM_PROMPT

    def test_has_gap_analysis_closer(self) -> None:
        """If the vault couldn't answer, the agent must name a candidate /note."""
        assert "candidate for" in ASK_SYSTEM_PROMPT
        assert "/note" in ASK_SYSTEM_PROMPT

    def test_only_references_allowlisted_tools(self) -> None:
        referenced = set(VAULT_TOOL_RE.findall(ASK_SYSTEM_PROMPT))
        assert referenced, "expected ASK_SYSTEM_PROMPT to reference at least one vault_* tool"
        allowed = _allowlisted_vault_tools(TOOLS_ASK)
        for name in referenced:
            assert name in allowed, (
                f"ASK_SYSTEM_PROMPT references {name!r} but it is not in TOOLS_ASK"
            )

    def test_allowlist_includes_load_bearing_tools(self) -> None:
        """Read-side load-bearing tools the /ask recipe relies on must remain allowlisted."""
        required = [
            "mcp__obsidian-pkm__vault_semantic_search",
            "mcp__obsidian-pkm__vault_neighborhood",
            "mcp__obsidian-pkm__vault_read",
            "mcp__obsidian-pkm__vault_links",
        ]
        for name in required:
            assert name in TOOLS_ASK, f"{name} missing from TOOLS_ASK"


class TestChatSystemPrompt:
    def test_prefers_semantic_search_and_neighborhood(self) -> None:
        assert "vault_semantic_search" in CHAT_SYSTEM_PROMPT
        assert "vault_neighborhood" in CHAT_SYSTEM_PROMPT

    def test_only_references_allowlisted_tools(self) -> None:
        """The chat profile uses TOOLS_ASK (read-only)."""
        referenced = set(VAULT_TOOL_RE.findall(CHAT_SYSTEM_PROMPT))
        assert referenced, "expected CHAT_SYSTEM_PROMPT to reference at least one vault_* tool"
        allowed = _allowlisted_vault_tools(TOOLS_ASK)
        for name in referenced:
            assert name in allowed, (
                f"CHAT_SYSTEM_PROMPT references {name!r} but it is not in TOOLS_ASK"
            )

    def test_allowlist_includes_load_bearing_tools(self) -> None:
        """Read-side load-bearing tools the /chat recipe relies on must remain allowlisted."""
        required = [
            "mcp__obsidian-pkm__vault_semantic_search",
            "mcp__obsidian-pkm__vault_neighborhood",
            "mcp__obsidian-pkm__vault_read",
        ]
        for name in required:
            assert name in TOOLS_ASK, f"{name} missing from TOOLS_ASK"


class TestExtractionPromptRegression:
    """Smoke test: Phase 4 must not regress the Phase 1-3 extraction markers."""

    def test_phase_1_to_3_markers_intact(self) -> None:
        for marker in ("Stage 0", "Stage 1", "vault_add_links", "vault_suggest_links"):
            assert marker in EXTRACTION_SYSTEM_PROMPT, f"missing marker {marker!r}"


class TestLinkDiscoveryRecipeConsistency:
    """The link-discovery recipe must be the same across all write-capable prompts.

    The recipe lives as a module-level LINK_DISCOVERY_RECIPE constant in
    audio_ingest.prompts and is interpolated into NOTE, TASK, and EXTRACTION.
    This test asserts the canonical 8-verb list (with the 'related to'
    prohibition) appears verbatim in all three so a future prompt edit can't
    silently drift one of them.
    """

    def test_link_discovery_recipe_consistent_across_prompts(self) -> None:
        # Discriminator: a chunk of the canonical 8 relationship verbs that
        # would only appear if the shared recipe is in the prompt.
        discriminator = (
            "builds-on, supersedes, implements, contradicts, extends, refines, "
            "provides-context-for, is-an-instance-of"
        )
        assert discriminator in NOTE_SYSTEM_PROMPT, (
            "NOTE_SYSTEM_PROMPT is missing the canonical 8-verb relationship list"
        )
        assert discriminator in TASK_SYSTEM_PROMPT, (
            "TASK_SYSTEM_PROMPT is missing the canonical 8-verb relationship list"
        )
        assert discriminator in EXTRACTION_SYSTEM_PROMPT, (
            "EXTRACTION_SYSTEM_PROMPT is missing the canonical 8-verb relationship list"
        )

    def test_related_to_prohibition_consistent_across_prompts(self) -> None:
        """The 'never write a vague related to' guardrail must appear in all three."""
        prohibition = "'related to'"
        assert prohibition in NOTE_SYSTEM_PROMPT
        assert prohibition in TASK_SYSTEM_PROMPT
        # The EXTRACTION prompt's prohibition is preserved through the shared
        # LINK_DISCOVERY_RECIPE constant interpolation.
        assert prohibition in EXTRACTION_SYSTEM_PROMPT
