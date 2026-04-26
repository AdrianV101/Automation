import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from audio_ingest.extraction import (
    AgentRoutingResult,
    SYSTEM_PROMPT,
    _build_user_prompt,
    _find_summary_path,
    agent_extract_and_route,
)
from audio_ingest.tools import TOOLS_EXTRACTION
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
        assert opts.max_turns is None
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

        with patch("audio_ingest.extraction.run_agent_loop_streaming", side_effect=mock_streaming):
            with patch("audio_ingest.extraction.TelegramStreamSender") as MockSender:
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

        with patch("audio_ingest.extraction.run_agent_loop_streaming", side_effect=mock_streaming):
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
    """Guard the SYSTEM_PROMPT against drift away from the pkm-write workflow."""

    def test_prompt_invokes_dedup_before_write(self) -> None:
        """Dedup gate must be in the prompt with a 0.8 similarity threshold."""
        assert "vault_semantic_search" in SYSTEM_PROMPT
        assert "0.8" in SYSTEM_PROMPT

    def test_prompt_uses_suggest_and_add_links(self) -> None:
        """The link-discovery + link-insertion pair must be referenced."""
        assert "vault_suggest_links" in SYSTEM_PROMPT
        assert "vault_add_links" in SYSTEM_PROMPT

    def test_prompt_lists_content_type_template_table(self) -> None:
        """All structured-note templates must appear so the agent can pick them."""
        for template in [
            "research-note",
            "adr",
            "task",
            "troubleshooting-log",
            "meeting-notes",
            "permanent-note",
        ]:
            assert template in SYSTEM_PROMPT, f"missing template {template} in SYSTEM_PROMPT"

    def test_inbox_summary_skips_dedup(self) -> None:
        """Audio-ingestion inbox note must always be created, bypassing dedup."""
        assert "ALWAYS create" in SYSTEM_PROMPT
        assert "audio-ingestion" in SYSTEM_PROMPT
        # Make sure the inbox path appears so a regression that drops the carve-out fails.
        assert "00-Inbox/audio-ingestion/" in SYSTEM_PROMPT

    def test_prompt_only_references_allowlisted_tools(self) -> None:
        """Every vault_* tool referenced in the prompt must be in TOOLS_EXTRACTION."""
        referenced = set(re.findall(r"vault_[a-z_]+", SYSTEM_PROMPT))
        # Strip trailing underscores in case any regex artifact slips in.
        referenced = {t.rstrip("_") for t in referenced}
        assert referenced, "expected SYSTEM_PROMPT to reference at least one vault_* tool"
        prefixed_allowlist = set(TOOLS_EXTRACTION)
        for name in referenced:
            full = f"mcp__obsidian-pkm__{name}"
            assert full in prefixed_allowlist, (
                f"SYSTEM_PROMPT references {name!r} but it is not in TOOLS_EXTRACTION"
            )
