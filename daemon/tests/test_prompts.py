"""Tests for the dynamic system-prompt builders.

Combines:
- Main's roster/builder shape tests (empty/populated, MUST-NOT framing)
- PR's vault-pkm dogfood discipline guards (link-discovery recipe consistency,
  allowlist drift, fleeting-note/task template anchors, gap-analysis closer,
  Phase 1-3 extraction marker regression).

After the PR-into-main merge, prompts are dynamic builders rather than
module-level constants, so PR's class-based assertions now invoke the builders
with a tmp_path fixture before pattern-matching.
"""
import re
from pathlib import Path

import pytest

from audio_ingest.prompts import (
    build_ask_system_prompt,
    build_chat_system_prompt,
    build_extraction_system_prompt,
    build_note_system_prompt,
    build_task_system_prompt,
)
from audio_ingest.tools import TOOLS_ASK, TOOLS_TASK


VAULT_TOOL_RE = re.compile(r"\bvault_[a-z]+(?:_[a-z]+)*\b")


def _allowlisted_vault_tools(profile: list[str]) -> set[str]:
    """Strip the mcp__obsidian-pkm__ prefix to compare against prompt mentions."""
    prefix = "mcp__obsidian-pkm__"
    return {name[len(prefix):] for name in profile if name.startswith(prefix)}


def _seed_person(vault: Path, name: str, relationship: str, description: str) -> None:
    folder = vault / "03-Resources" / "People"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(
        "---\n"
        "type: person\n"
        f"relationship: \"{relationship}\"\n"
        f"description: \"{description}\"\n"
        "tags: [person]\n"
        "---\n"
        f"# {name}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Roster / builder shape (from main)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("builder", [
    build_note_system_prompt,
    build_task_system_prompt,
    build_ask_system_prompt,
    build_chat_system_prompt,
])
def test_empty_roster_renders_none(builder, tmp_path):
    prompt = builder(tmp_path)
    assert "Known people: (none)" in prompt


@pytest.mark.parametrize("builder", [
    build_note_system_prompt,
    build_task_system_prompt,
    build_ask_system_prompt,
    build_chat_system_prompt,
])
def test_populated_roster_renders_oneliner(builder, tmp_path):
    _seed_person(tmp_path, "Adrian Verhoosel Azpiroz", "self / system owner", "the user")
    prompt = builder(tmp_path)
    assert "Adrian Verhoosel Azpiroz (self / system owner)" in prompt
    assert prompt.count("Adrian Verhoosel Azpiroz (self / system owner)") == 1


def test_note_prompt_keeps_critical_constraints(tmp_path):
    prompt = build_note_system_prompt(tmp_path)
    assert "MUST NOT" in prompt
    assert "vault_semantic_search" in prompt


# ---------------------------------------------------------------------------
# vault-pkm dogfood discipline guards (from PR, adapted to builders)
# ---------------------------------------------------------------------------

class TestNoteSystemPrompt:
    def test_invokes_post_write_link_discovery(self, tmp_path) -> None:
        """After writing the note, the agent must run vault_suggest_links and vault_add_links."""
        prompt = build_note_system_prompt(tmp_path)
        assert "vault_suggest_links" in prompt
        assert "vault_add_links" in prompt

    def test_fleeting_note_template_is_explicit(self, tmp_path) -> None:
        """The fleeting-note template name must be visible to the agent.

        Asserts against the literal `template 'fleeting-note'` string rather than
        the bare template name, so a reviewer mentioning 'fleeting-note' in prose
        without the `vault_write with template 'fleeting-note'` instruction would
        fail this guard.
        """
        prompt = build_note_system_prompt(tmp_path)
        assert "template 'fleeting-note'" in prompt

    def test_keeps_filing_clerk_framing(self, tmp_path) -> None:
        """Phase 4 must preserve the CRITICAL CONSTRAINTS / filing clerk language."""
        prompt = build_note_system_prompt(tmp_path)
        assert "CRITICAL CONSTRAINTS" in prompt
        assert "filing clerk" in prompt

    def test_keeps_single_line_response_format(self, tmp_path) -> None:
        """The SINGLE-LINE response format must be preserved."""
        assert "SINGLE LINE" in build_note_system_prompt(tmp_path)

    def test_only_references_allowlisted_tools(self, tmp_path) -> None:
        """Every vault_* token in the prompt must be in TOOLS_TASK (the /note profile)."""
        prompt = build_note_system_prompt(tmp_path)
        referenced = set(VAULT_TOOL_RE.findall(prompt))
        assert referenced, "expected NOTE prompt to reference at least one vault_* tool"
        allowed = _allowlisted_vault_tools(TOOLS_TASK)
        for name in referenced:
            assert name in allowed, (
                f"NOTE prompt references {name!r} but it is not in TOOLS_TASK"
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
    def test_invokes_post_write_link_discovery(self, tmp_path) -> None:
        prompt = build_task_system_prompt(tmp_path)
        assert "vault_suggest_links" in prompt
        assert "vault_add_links" in prompt

    def test_references_task_template(self, tmp_path) -> None:
        """Assert against the literal template-name string, not the bare word
        'task' (which appears in prose as 'task CAPTURE agent', 'task note', etc.
        and would make this guard trivially true).
        """
        assert "template 'task'" in build_task_system_prompt(tmp_path)

    def test_lists_status_enum(self, tmp_path) -> None:
        prompt = build_task_system_prompt(tmp_path)
        for keyword in ("pending", "active", "done", "cancelled"):
            assert keyword in prompt, f"missing status enum value: {keyword}"

    def test_keeps_critical_constraints_and_response_format(self, tmp_path) -> None:
        prompt = build_task_system_prompt(tmp_path)
        assert "CRITICAL CONSTRAINTS" in prompt
        assert "SINGLE LINE" in prompt

    def test_only_references_allowlisted_tools(self, tmp_path) -> None:
        prompt = build_task_system_prompt(tmp_path)
        referenced = set(VAULT_TOOL_RE.findall(prompt))
        assert referenced, "expected TASK prompt to reference at least one vault_* tool"
        allowed = _allowlisted_vault_tools(TOOLS_TASK)
        for name in referenced:
            assert name in allowed, (
                f"TASK prompt references {name!r} but it is not in TOOLS_TASK"
            )

    def test_allowlist_includes_load_bearing_tools(self) -> None:
        """Catches a future edit that removes a load-bearing tool from BOTH the
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
    def test_keeps_semantic_search_primary(self, tmp_path) -> None:
        assert "vault_semantic_search" in build_ask_system_prompt(tmp_path)

    def test_has_gap_analysis_closer(self, tmp_path) -> None:
        """If the vault couldn't answer, the agent must name a candidate /note."""
        prompt = build_ask_system_prompt(tmp_path)
        assert "candidate for" in prompt.lower() or "Candidate for" in prompt
        assert "/note" in prompt

    def test_only_references_allowlisted_tools(self, tmp_path) -> None:
        prompt = build_ask_system_prompt(tmp_path)
        referenced = set(VAULT_TOOL_RE.findall(prompt))
        assert referenced, "expected ASK prompt to reference at least one vault_* tool"
        allowed = _allowlisted_vault_tools(TOOLS_ASK)
        for name in referenced:
            assert name in allowed, (
                f"ASK prompt references {name!r} but it is not in TOOLS_ASK"
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
    def test_prefers_semantic_search_and_neighborhood(self, tmp_path) -> None:
        prompt = build_chat_system_prompt(tmp_path)
        assert "vault_semantic_search" in prompt
        assert "vault_neighborhood" in prompt

    def test_only_references_allowlisted_tools(self, tmp_path) -> None:
        """The chat profile uses TOOLS_ASK (read-only)."""
        prompt = build_chat_system_prompt(tmp_path)
        referenced = set(VAULT_TOOL_RE.findall(prompt))
        assert referenced, "expected CHAT prompt to reference at least one vault_* tool"
        allowed = _allowlisted_vault_tools(TOOLS_ASK)
        for name in referenced:
            assert name in allowed, (
                f"CHAT prompt references {name!r} but it is not in TOOLS_ASK"
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

    def test_phase_1_to_3_markers_intact(self, tmp_path) -> None:
        prompt = build_extraction_system_prompt(tmp_path)
        for marker in ("Stage 0", "Stage 1", "vault_add_links", "vault_suggest_links"):
            assert marker in prompt, f"missing marker {marker!r}"


class TestLinkDiscoveryRecipeConsistency:
    """The link-discovery recipe must be the same across all write-capable prompts.

    The recipe lives as a module-level LINK_DISCOVERY_RECIPE constant in
    audio_ingest.prompts and is interpolated into NOTE, TASK, and EXTRACTION.
    This test asserts the canonical 8-verb list (with the 'related to'
    prohibition) appears verbatim in all three so a future prompt edit can't
    silently drift one of them.
    """

    def test_link_discovery_recipe_consistent_across_prompts(self, tmp_path) -> None:
        # Discriminator: a chunk of the canonical 8 relationship verbs that
        # would only appear if the shared recipe is in the prompt.
        discriminator = (
            "builds-on, supersedes, implements, contradicts, extends, refines, "
            "provides-context-for, is-an-instance-of"
        )
        note = build_note_system_prompt(tmp_path)
        task = build_task_system_prompt(tmp_path)
        extraction = build_extraction_system_prompt(tmp_path)
        assert discriminator in note, (
            "NOTE prompt is missing the canonical 8-verb relationship list"
        )
        assert discriminator in task, (
            "TASK prompt is missing the canonical 8-verb relationship list"
        )
        assert discriminator in extraction, (
            "EXTRACTION prompt is missing the canonical 8-verb relationship list"
        )

    def test_related_to_prohibition_consistent_across_prompts(self, tmp_path) -> None:
        """The 'never write a vague related to' guardrail must appear in all three."""
        prohibition = "'related to'"
        assert prohibition in build_note_system_prompt(tmp_path)
        assert prohibition in build_task_system_prompt(tmp_path)
        # The EXTRACTION prompt's prohibition is preserved through the shared
        # LINK_DISCOVERY_RECIPE constant interpolation.
        assert prohibition in build_extraction_system_prompt(tmp_path)

    def test_recipe_comes_from_shared_constant_not_copy_paste(self, tmp_path) -> None:
        """The recipe must be sourced from LINK_DISCOVERY_RECIPE, not copy-pasted.

        The verbatim-discriminator test above would still pass if a developer
        "fixed" the recipe in NOTE only by editing the prompt body directly
        (bypassing the constant). This test imports the constant and asserts
        each builder produces output that contains the constant rendered with
        the appropriate path_label -- so a copy-paste that drifted from the
        constant would fail here.
        """
        from audio_ingest.prompts import LINK_DISCOVERY_RECIPE

        note_recipe = LINK_DISCOVERY_RECIPE.format(path_label="note path")
        task_recipe = LINK_DISCOVERY_RECIPE.format(path_label="task path")
        extraction_recipe = LINK_DISCOVERY_RECIPE.format(path_label="new note path")

        assert note_recipe in build_note_system_prompt(tmp_path), (
            "NOTE prompt's recipe diverges from LINK_DISCOVERY_RECIPE constant"
        )
        assert task_recipe in build_task_system_prompt(tmp_path), (
            "TASK prompt's recipe diverges from LINK_DISCOVERY_RECIPE constant"
        )
        assert extraction_recipe in build_extraction_system_prompt(tmp_path), (
            "EXTRACTION prompt's recipe diverges from LINK_DISCOVERY_RECIPE constant"
        )
