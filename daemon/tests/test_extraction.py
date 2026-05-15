import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from automation_daemon.extraction import (
    AgentRoutingResult,
    _build_user_prompt,
    _find_summary_path,
    agent_extract_and_route,
)
from automation_daemon.prompts import build_extraction_system_prompt
from automation_daemon.tools import TOOLS_EXTRACTION
from agent_infra import AgentLoopResult
from pkm import TranscriptData, TranscriptSegment

from conftest import (
    MockAssistantMessage,
    MockTextBlock,
    MockToolUseBlock,
    make_assistant_message,
)


@pytest.fixture
def pkm_vault_path(tmp_path):
    return tmp_path / "pkm"


@pytest.fixture
def transcript():
    return TranscriptData(
        job_id="test-job",
        recorded_at="2026-02-06T12:00:00+00:00",
        duration_seconds=120.0,
        speakers=["Alice", "Bob"],
        segments=[
            TranscriptSegment(0.0, 5.0, "Alice", "Let's discuss the roadmap."),
            TranscriptSegment(5.0, 10.0, "Bob", "Sounds good."),
        ],
        full_text="Alice: Let's discuss the roadmap.\nBob: Sounds good.",
    )


@pytest.fixture
def transcript_path(tmp_path):
    return tmp_path / "pkm" / "04-Archive" / "transcripts" / "2026" / "02" / "2026-02-06-test-job.md"


class TestBuildUserPrompt:
    def test_prompt_contains_transcript_text(self, transcript, transcript_path):
        prompt = _build_user_prompt(transcript, transcript_path)
        assert "Let's discuss the roadmap." in prompt
        assert "Sounds good." in prompt

    def test_prompt_contains_metadata(self, transcript, transcript_path):
        prompt = _build_user_prompt(transcript, transcript_path)
        assert "2026-02-06" in prompt
        assert "2 minutes" in prompt or "2" in prompt
        assert "Alice, Bob" in prompt

    def test_prompt_contains_transcript_link(self, transcript, transcript_path):
        prompt = _build_user_prompt(transcript, transcript_path)
        assert "[[2026-02-06-test-job]]" in prompt


class TestFindSummaryPath:
    def test_finds_inbox_path(self):
        files = [
            "01-Projects/Automation/devlog.md",
            "00-Inbox/audio-ingestion/2026-02-06-roadmap.md",
        ]
        assert _find_summary_path(files) == "00-Inbox/audio-ingestion/2026-02-06-roadmap.md"

    def test_returns_none_when_no_inbox(self):
        files = ["01-Projects/Automation/devlog.md"]
        assert _find_summary_path(files) is None

    def test_empty_list(self):
        assert _find_summary_path([]) is None


class TestAgentRoutingResultInvariants:
    """The success/error pair invariant (type-design-analyzer I4) prevents
    misconstructions that would silently mislead operators."""

    def test_success_true_with_error_is_rejected(self):
        with pytest.raises(ValueError, match="success=True.*error=None"):
            AgentRoutingResult(success=True, summary="ok", error="actually failed")

    def test_success_false_without_error_is_rejected(self):
        with pytest.raises(ValueError, match="success=False.*non-None error"):
            AgentRoutingResult(success=False, summary="oops")

    def test_success_true_without_error_is_valid(self):
        r = AgentRoutingResult(success=True, summary="ok")
        assert r.error is None

    def test_success_false_with_error_is_valid(self):
        r = AgentRoutingResult(success=False, summary="", error="something broke")
        assert r.error == "something broke"


class TestAgentExtractAndRoute:
    async def test_successful_routing(self, pkm_vault_path, transcript, transcript_path):
        """Agent returns text + tool calls -> success."""
        msg = make_assistant_message(
            text_blocks=["Routed 3 items to PKM."],
            tool_blocks=[
                ("mcp__obsidian-pkm__vault_write", {"path": "00-Inbox/audio-ingestion/2026-02-06-roadmap.md"}),
                ("mcp__obsidian-pkm__vault_append", {"path": "01-Projects/Automation/devlog.md", "content": "task"}),
            ],
        )

        async def mock_query(prompt, options):
            yield msg

        with (
            patch("agent_infra.runner.query", side_effect=mock_query),
            patch("agent_infra.runner.AssistantMessage", MockAssistantMessage),
            patch("agent_infra.runner.TextBlock", MockTextBlock),
            patch("agent_infra.runner.ToolUseBlock", MockToolUseBlock),
        ):
            result = await agent_extract_and_route(transcript, transcript_path, pkm_vault_path)

        assert result.success is True
        assert "Routed 3 items" in result.summary
        assert len(result.files_written) == 2
        assert result.summary_path == "00-Inbox/audio-ingestion/2026-02-06-roadmap.md"
        assert result.error is None

    async def test_no_files_written(self, pkm_vault_path, transcript, transcript_path):
        """Agent runs but writes nothing -> failure."""
        msg = make_assistant_message(text_blocks=["I couldn't find anything to extract."])

        async def mock_query(prompt, options):
            yield msg

        with (
            patch("agent_infra.runner.query", side_effect=mock_query),
            patch("agent_infra.runner.AssistantMessage", MockAssistantMessage),
            patch("agent_infra.runner.TextBlock", MockTextBlock),
            patch("agent_infra.runner.ToolUseBlock", MockToolUseBlock),
        ):
            result = await agent_extract_and_route(transcript, transcript_path, pkm_vault_path)

        assert result.success is False
        assert "wrote no files" in result.error

    async def test_query_exception(self, pkm_vault_path, transcript, transcript_path):
        """Agent SDK raises -> graceful failure."""
        async def mock_query(prompt, options):
            raise RuntimeError("CLI not found")
            yield  # make it an async generator  # noqa: unreachable

        with (
            patch("agent_infra.runner.query", side_effect=mock_query),
            patch("agent_infra.runner.AssistantMessage", MockAssistantMessage),
            patch("agent_infra.runner.TextBlock", MockTextBlock),
            patch("agent_infra.runner.ToolUseBlock", MockToolUseBlock),
        ):
            result = await agent_extract_and_route(transcript, transcript_path, pkm_vault_path)

        assert result.success is False
        assert "Agent SDK query failed" in result.error

    async def test_no_files_branch_surfaces_tool_errors(
        self, pkm_vault_path, transcript, transcript_path,
    ):
        """When agent finishes with no files written but tool errors were
        observed, the breadcrumbs must surface in the result error message
        (silent-failure-hunter C1)."""
        from unittest.mock import AsyncMock
        loop_result = AgentLoopResult(
            text_parts=["I gave up."],
            files_written=[],
            turns_used=20,
            error=None,
            tool_errors=[
                "MCP error: vault frontmatter validation failed",
                "MCP error: path 00-Inbox/audio-ingestion/2026-04-26.md not writable",
            ],
        )
        with patch(
            "automation_daemon.extraction.run_agent_loop_streaming",
            new=AsyncMock(return_value=loop_result),
        ):
            result = await agent_extract_and_route(transcript, transcript_path, pkm_vault_path)

        assert result.success is False
        assert "wrote no files" in result.error
        assert "turns=20" in result.error
        assert "MCP error" in result.error

    async def test_extraction_options_pass_max_turns_150(
        self, pkm_vault_path, transcript, transcript_path,
    ):
        """Extraction's turn cap must be sized for the multi-stage prompt
        (Stage 0 + Stage 1 + per-item write protocol)."""
        captured = {}

        async def mock_query(prompt, options):
            captured["options"] = options
            yield make_assistant_message(
                tool_blocks=[(
                    "mcp__obsidian-pkm__vault_write",
                    {"path": "00-Inbox/audio-ingestion/x.md"},
                )],
            )

        with (
            patch("agent_infra.runner.query", side_effect=mock_query),
            patch("agent_infra.runner.AssistantMessage", MockAssistantMessage),
            patch("agent_infra.runner.TextBlock", MockTextBlock),
            patch("agent_infra.runner.ToolUseBlock", MockToolUseBlock),
        ):
            await agent_extract_and_route(transcript, transcript_path, pkm_vault_path)

        assert captured["options"].max_turns == 150

    async def test_routing_result_propagates_aux_path_lists(
        self, pkm_vault_path, transcript, transcript_path,
    ):
        """Frontmatter and link aux mutations from the dedup-hit branch must
        survive the trip from AgentLoopResult to AgentRoutingResult so the
        pipeline can audit them.
        """
        from unittest.mock import AsyncMock
        loop_result = AgentLoopResult(
            text_parts=["routed"],
            files_written=["00-Inbox/audio-ingestion/x.md"],
            turns_used=20,
            frontmatter_updated=["01-Projects/A/research/topic.md"],
            links_added=[
                "01-Projects/A/research/topic.md",
                "01-Projects/A/_index.md",
            ],
        )
        with patch(
            "automation_daemon.extraction.run_agent_loop_streaming",
            new=AsyncMock(return_value=loop_result),
        ):
            result = await agent_extract_and_route(transcript, transcript_path, pkm_vault_path)

        assert result.success is True
        assert result.frontmatter_updated == ["01-Projects/A/research/topic.md"]
        assert result.links_added == [
            "01-Projects/A/research/topic.md",
            "01-Projects/A/_index.md",
        ]

    async def test_deduplicates_file_paths(self, pkm_vault_path, transcript, transcript_path):
        """Same file written twice should appear once in files_written."""
        msg = make_assistant_message(
            tool_blocks=[
                ("mcp__obsidian-pkm__vault_write", {"path": "00-Inbox/audio-ingestion/test.md"}),
                ("mcp__obsidian-pkm__vault_append", {"path": "00-Inbox/audio-ingestion/test.md", "content": "more"}),
            ],
        )

        async def mock_query(prompt, options):
            yield msg

        with (
            patch("agent_infra.runner.query", side_effect=mock_query),
            patch("agent_infra.runner.AssistantMessage", MockAssistantMessage),
            patch("agent_infra.runner.TextBlock", MockTextBlock),
            patch("agent_infra.runner.ToolUseBlock", MockToolUseBlock),
        ):
            result = await agent_extract_and_route(transcript, transcript_path, pkm_vault_path)

        assert result.success is True
        assert len(result.files_written) == 1

    async def test_options_configuration(self, pkm_vault_path, transcript, transcript_path):
        """Verify the agent options are set correctly."""
        captured_options = {}

        async def mock_query(prompt, options):
            captured_options["prompt"] = prompt
            captured_options["options"] = options
            msg = make_assistant_message(
                tool_blocks=[
                    ("mcp__obsidian-pkm__vault_write", {"path": "00-Inbox/audio-ingestion/test.md"}),
                ],
            )
            yield msg

        with (
            patch("agent_infra.runner.query", side_effect=mock_query),
            patch("agent_infra.runner.AssistantMessage", MockAssistantMessage),
            patch("agent_infra.runner.TextBlock", MockTextBlock),
            patch("agent_infra.runner.ToolUseBlock", MockToolUseBlock),
        ):
            await agent_extract_and_route(transcript, transcript_path, pkm_vault_path)

        opts = captured_options["options"]
        assert opts.permission_mode == "bypassPermissions"
        assert opts.max_turns == 150
        assert opts.model == "claude-opus-4-6"
        assert "obsidian-pkm" in opts.mcp_servers
        assert opts.mcp_servers["obsidian-pkm"]["type"] == "stdio"
        assert str(pkm_vault_path) in opts.mcp_servers["obsidian-pkm"]["env"]["VAULT_PATH"]

    async def test_streams_when_tg_provided(self, tmp_path, transcript, transcript_path):
        """When tg config is provided, TelegramStreamSender is wired up."""
        from telegram_interface import BotConfig

        mock_result = AgentLoopResult(
            text_parts=["Summary."],
            files_written=["00-Inbox/audio-ingestion/note.md"],
            turns_used=2,
        )

        async def mock_streaming(prompt, options, on_event=None):
            return mock_result

        with patch("automation_daemon.extraction.run_agent_loop_streaming", side_effect=mock_streaming):
            with patch("automation_daemon.extraction.TelegramStreamSender") as MockSender:
                mock_sender_instance = MockSender.return_value
                mock_sender_instance.handle = AsyncMock()
                mock_sender_instance.flush = AsyncMock()

                tg = BotConfig(bot_token="fake", chat_id="123")
                result = await agent_extract_and_route(
                    transcript, transcript_path, tmp_path / "pkm",
                    tg=tg, thread_id=42,
                )

                MockSender.assert_called_once_with(tg, 42)
                mock_sender_instance.flush.assert_awaited_once()

        assert result.success

    async def test_no_streaming_without_tg(self, tmp_path, transcript, transcript_path):
        """When tg is None, no TelegramStreamSender is created."""
        mock_result = AgentLoopResult(
            text_parts=["Summary."],
            files_written=["00-Inbox/audio-ingestion/note.md"],
            turns_used=2,
        )

        async def mock_streaming(prompt, options, on_event=None):
            assert on_event is None
            return mock_result

        with patch("automation_daemon.extraction.run_agent_loop_streaming", side_effect=mock_streaming):
            result = await agent_extract_and_route(
                transcript, transcript_path, tmp_path / "pkm",
            )

        assert result.success


def _transcript() -> TranscriptData:
    return TranscriptData(
        job_id="x",
        recorded_at="2024-01-01T00:00:00Z",
        duration_seconds=120.0,
        speakers=["Speaker_1"],
        segments=[TranscriptSegment(start=0, end=5, speaker="Speaker_1", text="note")],
        full_text="note",
    )


def test_prompt_includes_plaud_summaries_when_provided() -> None:
    prompt = _build_user_prompt(
        _transcript(),
        Path("/vault/t.md"),
        source_metadata={
            "plaud_summaries": {"Voice Note": "- [ ] do thing\n- [ ] other thing"},
        },
    )
    assert "Plaud" in prompt
    assert "Voice Note" in prompt
    assert "do thing" in prompt


def test_prompt_mentions_infographic_when_provided() -> None:
    prompt = _build_user_prompt(
        _transcript(),
        Path("/vault/t.md"),
        source_metadata={"infographic_path": Path("99-Attachments/plaud/abc.jpg")},
    )
    assert "99-Attachments/plaud/abc.jpg" in prompt


def test_prompt_omits_plaud_block_when_no_summaries() -> None:
    prompt = _build_user_prompt(
        _transcript(),
        Path("/vault/t.md"),
        source_metadata=None,
    )
    assert "Plaud has already produced" not in prompt


class TestSystemPromptContents:
    """Guard the extraction prompt against drift away from the pkm-write workflow.

    The extraction prompt is built per-call via build_extraction_system_prompt
    so it can pull the live People roster from the vault. These tests build it
    once per method against a fresh tmp_path and assert against the result.
    """

    @pytest.fixture
    def prompt(self, tmp_path) -> str:
        return build_extraction_system_prompt(tmp_path)

    def test_prompt_invokes_dedup_before_write(self, prompt) -> None:
        """Dedup gate must be in the prompt with a 0.8 similarity threshold."""
        assert "vault_semantic_search" in prompt
        assert "0.8" in prompt

    def test_prompt_uses_suggest_and_add_links(self, prompt) -> None:
        """The link-discovery + link-insertion pair must be referenced."""
        assert "vault_suggest_links" in prompt
        assert "vault_add_links" in prompt

    def test_prompt_lists_content_type_template_table(self, prompt) -> None:
        """All structured-note templates must appear so the agent can pick them."""
        for template in [
            "research-note",
            "adr",
            "task",
            "troubleshooting-log",
            "meeting-notes",
            "permanent-note",
        ]:
            assert template in prompt, f"missing template {template} in extraction prompt"

    def test_inbox_summary_skips_dedup(self, prompt) -> None:
        """Audio-ingestion inbox note must always be created, bypassing dedup."""
        assert "ALWAYS create" in prompt
        assert "audio-ingestion" in prompt
        # Make sure the inbox path appears so a regression that drops the carve-out fails.
        assert "00-Inbox/audio-ingestion/" in prompt

    def test_prompt_only_references_allowlisted_tools(self, prompt) -> None:
        """Every vault_* tool referenced in the prompt must be in TOOLS_EXTRACTION."""
        referenced = set(re.findall(r"\bvault_[a-z]+(?:_[a-z]+)*\b", prompt))
        assert referenced, "expected extraction prompt to reference at least one vault_* tool"
        prefixed_allowlist = set(TOOLS_EXTRACTION)
        for name in referenced:
            full = f"mcp__obsidian-pkm__{name}"
            assert full in prefixed_allowlist, (
                f"extraction prompt references {name!r} but it is not in TOOLS_EXTRACTION"
            )

    def test_prompt_calls_vault_activity_first(self, prompt) -> None:
        """Same-batch dedup: the Stage 0 block must precede any vault_write."""
        assert "vault_activity" in prompt
        assert "Stage 0" in prompt
        assert prompt.find("Stage 0") < prompt.find("vault_write"), (
            "Stage 0 (same-batch dedup) must be introduced before the first "
            "vault_write reference so the agent reads it first"
        )
        lowered = prompt.lower()
        assert any(
            phrase in lowered
            for phrase in ("already covered", "already routed", "already written")
        ), "expected same-batch dedup phrasing (already covered/routed/written)"

    def test_prompt_runs_pre_write_vault_sweep(self, prompt) -> None:
        """Stage 1 vault sweep must sit between Stage 0 and the Routing Rules."""
        assert "vault_neighborhood" in prompt
        assert "Stage 1" in prompt
        assert prompt.find("Stage 1") < prompt.find("## Routing Rules"), (
            "Stage 1 (vault context sweep) must appear before the Routing Rules section"
        )
        assert prompt.find("Stage 0") < prompt.find("Stage 1"), (
            "Stage 1 must come after Stage 0 so the agent reads dedup first, then the sweep"
        )
        lowered = prompt.lower()
        assert any(
            phrase in lowered
            for phrase in ("before routing", "existing vault state", "existing notes")
        ), "expected Stage 1 phrasing (before routing / existing vault state / existing notes)"

    def test_per_item_write_protocol_has_five_steps_in_order(self, prompt) -> None:
        """All 5 steps of the per-item write protocol must be present in order.

        Without this, a regression that dropped step 5 (bidirectional linking)
        entirely or that flipped which template families get backlinks would
        not fail any other test. Step markers are the literal '1. **',
        '2. **', etc. that the prompt emits as Markdown bullets.
        """
        markers = [
            "1. **Dedup check.**",
            "2. **Pick the template**",
            "3. **Create the note**",
            "4. **Discover, annotate, and insert links.**",
            "5. **Bidirectional linking.**",
        ]
        positions: list[int] = []
        for marker in markers:
            idx = prompt.find(marker)
            assert idx != -1, f"per-item write protocol missing marker: {marker!r}"
            positions.append(idx)
        assert positions == sorted(positions), (
            f"per-item write protocol steps out of order: {positions}"
        )

    def test_bidirectional_linking_carve_out_pinned(self, prompt) -> None:
        """Step 5's template-family carve-out must list the structured templates
        that get backlinks, AND must explicitly skip ephemeral templates.

        A regression that dropped 'ADRs' from the backlink list (or added
        'task' to it) would not be caught by the in-order check above.
        """
        # Templates that MUST get backlinks
        for template in ("ADRs", "research-notes", "meeting-notes",
                         "troubleshooting-logs", "permanent-notes"):
            assert template in prompt, (
                f"step 5 (bidirectional linking) must list {template!r} "
                f"as receiving backlinks"
            )
        # Templates that MUST be skipped
        skip_section_start = prompt.find("Skip this step for")
        assert skip_section_start != -1, "expected 'Skip this step for' carve-out in step 5"
        skip_section = prompt[skip_section_start : skip_section_start + 200]
        for ephemeral in ("fleeting-note", "daily-note", "task"):
            assert ephemeral in skip_section, (
                f"step 5 must explicitly skip {ephemeral!r}"
            )
