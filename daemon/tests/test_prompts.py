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
        """The fleeting-note template name must be visible to the agent."""
        assert "fleeting-note" in NOTE_SYSTEM_PROMPT

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


class TestTaskSystemPrompt:
    def test_invokes_post_write_link_discovery(self) -> None:
        assert "vault_suggest_links" in TASK_SYSTEM_PROMPT
        assert "vault_add_links" in TASK_SYSTEM_PROMPT

    def test_references_task_template(self) -> None:
        assert "task" in TASK_SYSTEM_PROMPT  # template name

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


class TestExtractionPromptRegression:
    """Smoke test: Phase 4 must not regress the Phase 1-3 extraction markers."""

    def test_phase_1_to_3_markers_intact(self) -> None:
        for marker in ("Stage 0", "Stage 1", "vault_add_links", "vault_suggest_links"):
            assert marker in EXTRACTION_SYSTEM_PROMPT, f"missing marker {marker!r}"
