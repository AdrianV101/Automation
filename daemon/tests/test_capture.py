"""Phase 5 guards: config-gated session capture pass that appends a devlog entry.

Mirrors the structure of test_extraction.py::TestAgentExtractAndRoute. The
capture pass is OFF by default (DaemonConfig.enable_session_capture=False) and
runs after a successful extraction when the flag is on. It uses a strict
subset of vault tools: read-side plus vault_append and vault_add_links only.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from audio_ingest.capture import (
    CAPTURE_SYSTEM_PROMPT,
    CaptureResult,
    _build_user_prompt,
    agent_capture_session,
)
from audio_ingest.config import DaemonConfig
from audio_ingest.extraction import AgentRoutingResult
from audio_ingest.models import RecordingJob
from audio_ingest.tools import TOOLS_CAPTURE
from agent_infra import AgentLoopResult
from pkm import TranscriptData, TranscriptSegment

from conftest import (
    MockAssistantMessage,
    MockTextBlock,
    MockToolUseBlock,
    make_assistant_message,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pkm_vault_path(tmp_path):
    return tmp_path / "pkm"


@pytest.fixture
def transcript_path(tmp_path):
    return tmp_path / "pkm" / "04-Archive" / "transcripts" / "2026" / "04" / "2026-04-26-test.md"


@pytest.fixture
def routing_result_success() -> AgentRoutingResult:
    return AgentRoutingResult(
        success=True,
        summary="Routed roadmap discussion to PKM:\n- ADR-007 created\n- task added",
        files_written=[
            "00-Inbox/audio-ingestion/2026-04-26-roadmap.md",
            "01-Projects/Automation/development/decisions/ADR-007-streaming.md",
            "01-Projects/Automation/tasks/wire-streaming.md",
        ],
        summary_path="00-Inbox/audio-ingestion/2026-04-26-roadmap.md",
        turns_used=12,
    )


# ---------------------------------------------------------------------------
# (h) CaptureResult dataclass shape
# ---------------------------------------------------------------------------


class TestCaptureResultDataclass:
    def test_default_construction(self) -> None:
        r = CaptureResult(success=True, summary="ok")
        assert r.success is True
        assert r.summary == "ok"
        assert r.files_appended == []
        assert r.error is None
        assert r.turns_used == 0

    def test_full_construction(self) -> None:
        r = CaptureResult(
            success=False,
            summary="failed",
            files_appended=["01-Projects/Automation/development/devlog.md"],
            error="something broke",
            turns_used=3,
        )
        assert r.files_appended == ["01-Projects/Automation/development/devlog.md"]
        assert r.error == "something broke"
        assert r.turns_used == 3


# ---------------------------------------------------------------------------
# (f) TOOLS_CAPTURE strict-subset shape
# ---------------------------------------------------------------------------


class TestToolsCaptureSubset:
    def test_includes_vault_append(self) -> None:
        assert "mcp__obsidian-pkm__vault_append" in TOOLS_CAPTURE

    def test_includes_vault_add_links(self) -> None:
        assert "mcp__obsidian-pkm__vault_add_links" in TOOLS_CAPTURE

    def test_includes_read_side_tools(self) -> None:
        for tool in (
            "mcp__obsidian-pkm__vault_read",
            "mcp__obsidian-pkm__vault_activity",
            "mcp__obsidian-pkm__vault_recent",
            "mcp__obsidian-pkm__vault_search",
        ):
            assert tool in TOOLS_CAPTURE, f"missing read-side tool {tool}"

    def test_excludes_write_tools(self) -> None:
        """Capture must not be able to vault_write or otherwise mutate notes."""
        forbidden = [
            "mcp__obsidian-pkm__vault_write",
            "mcp__obsidian-pkm__vault_edit",
            "mcp__obsidian-pkm__vault_update_frontmatter",
        ]
        for tool in forbidden:
            assert tool not in TOOLS_CAPTURE, (
                f"{tool} must not be in TOOLS_CAPTURE -- capture is append-only"
            )

    def test_excludes_admin_tools(self) -> None:
        for tool in (
            "mcp__obsidian-pkm__vault_trash",
            "mcp__obsidian-pkm__vault_move",
        ):
            assert tool not in TOOLS_CAPTURE, (
                f"{tool} must not be in TOOLS_CAPTURE -- capture cannot reorganise vault"
            )


# ---------------------------------------------------------------------------
# (e) CAPTURE_SYSTEM_PROMPT contents
# ---------------------------------------------------------------------------


class TestCaptureSystemPrompt:
    def test_references_vault_append(self) -> None:
        assert "vault_append" in CAPTURE_SYSTEM_PROMPT

    def test_references_devlog_path(self) -> None:
        assert "01-Projects/Automation/development/devlog.md" in CAPTURE_SYSTEM_PROMPT

    def test_references_vault_activity(self) -> None:
        assert "vault_activity" in CAPTURE_SYSTEM_PROMPT

    def test_is_ascii(self) -> None:
        CAPTURE_SYSTEM_PROMPT.encode("ascii")  # raises if non-ascii

    def test_size_under_1_5_kb(self) -> None:
        assert len(CAPTURE_SYSTEM_PROMPT.encode("utf-8")) <= 1500, (
            "Capture prompt should stay tight; capture runs in 3-5 turns"
        )


# ---------------------------------------------------------------------------
# (g) Allowlist drift: every vault_* token in the prompt is in TOOLS_CAPTURE
# ---------------------------------------------------------------------------


class TestCapturePromptAllowlistDrift:
    def test_prompt_only_references_allowlisted_tools(self) -> None:
        referenced = set(re.findall(r"\bvault_[a-z]+(?:_[a-z]+)*\b", CAPTURE_SYSTEM_PROMPT))
        assert referenced, "expected CAPTURE_SYSTEM_PROMPT to reference at least one vault_* tool"
        prefixed_allowlist = set(TOOLS_CAPTURE)
        for name in referenced:
            full = f"mcp__obsidian-pkm__{name}"
            assert full in prefixed_allowlist, (
                f"CAPTURE_SYSTEM_PROMPT references {name!r} but it is not in TOOLS_CAPTURE"
            )


# ---------------------------------------------------------------------------
# agent_capture_session direct tests
# ---------------------------------------------------------------------------


class TestAgentCaptureSession:
    async def test_successful_capture(
        self, pkm_vault_path, routing_result_success, transcript_path,
    ) -> None:
        msg = make_assistant_message(
            text_blocks=["Devlog entry appended."],
            tool_blocks=[
                (
                    "mcp__obsidian-pkm__vault_append",
                    {"path": "01-Projects/Automation/development/devlog.md", "content": "## 2026-04-26"},
                ),
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
            result = await agent_capture_session(
                routing_result_success, transcript_path, pkm_vault_path,
            )

        assert result.success is True
        assert "01-Projects/Automation/development/devlog.md" in result.files_appended

    async def test_passes_capture_tool_allowlist(
        self, pkm_vault_path, routing_result_success, transcript_path,
    ) -> None:
        captured = {}

        async def mock_query(prompt, options):
            captured["options"] = options
            yield make_assistant_message(
                tool_blocks=[(
                    "mcp__obsidian-pkm__vault_append",
                    {"path": "01-Projects/Automation/development/devlog.md", "content": "x"},
                )],
            )

        with (
            patch("agent_infra.runner.query", side_effect=mock_query),
            patch("agent_infra.runner.AssistantMessage", MockAssistantMessage),
            patch("agent_infra.runner.TextBlock", MockTextBlock),
            patch("agent_infra.runner.ToolUseBlock", MockToolUseBlock),
        ):
            await agent_capture_session(
                routing_result_success, transcript_path, pkm_vault_path,
            )

        opts = captured["options"]
        assert "obsidian-pkm" in opts.mcp_servers
        # Check that the allowed tools match TOOLS_CAPTURE strictly
        # (the SDK options object stores the tool list under allowed_tools)
        assert set(opts.allowed_tools) == set(TOOLS_CAPTURE), (
            "agent_capture_session must use TOOLS_CAPTURE as its allowlist"
        )

    async def test_user_prompt_includes_files_written(
        self, pkm_vault_path, routing_result_success, transcript_path,
    ) -> None:
        prompt = _build_user_prompt(routing_result_success, transcript_path)
        for path in routing_result_success.files_written:
            assert path in prompt, f"expected files_written entry {path!r} in capture user prompt"

    async def test_user_prompt_includes_transcript_path(
        self, pkm_vault_path, routing_result_success, transcript_path,
    ) -> None:
        prompt = _build_user_prompt(routing_result_success, transcript_path)
        # The transcript wikilink should appear (stem-based to match extraction style)
        assert transcript_path.stem in prompt

    async def test_query_exception_returns_failure(
        self, pkm_vault_path, routing_result_success, transcript_path,
    ) -> None:
        async def mock_query(prompt, options):
            raise RuntimeError("CLI not found")
            yield  # pragma: no cover

        with (
            patch("agent_infra.runner.query", side_effect=mock_query),
            patch("agent_infra.runner.AssistantMessage", MockAssistantMessage),
            patch("agent_infra.runner.TextBlock", MockTextBlock),
            patch("agent_infra.runner.ToolUseBlock", MockToolUseBlock),
        ):
            result = await agent_capture_session(
                routing_result_success, transcript_path, pkm_vault_path,
            )

        assert result.success is False
        assert result.error is not None

    async def test_no_files_branch_surfaces_tool_errors(
        self, pkm_vault_path, routing_result_success, transcript_path,
    ) -> None:
        """Forbidden-tool rejection -> capture surfaces it in the error string.

        Without this, the operator sees "Capture pass appended no devlog entry"
        with no breadcrumb to the actual cause (silent-failure-hunter C1).
        """
        loop_result = AgentLoopResult(
            text_parts=["I tried to vault_write but it was rejected."],
            files_written=[],
            turns_used=4,
            error=None,
            tool_errors=["Tool 'vault_write' is not in allowed_tools list"],
        )
        with patch(
            "audio_ingest.capture.run_agent_loop_streaming",
            new=AsyncMock(return_value=loop_result),
        ):
            result = await agent_capture_session(
                routing_result_success, transcript_path, pkm_vault_path,
            )

        assert result.success is False
        assert "vault_write" in result.error
        assert "tool_errors" in result.error
        assert "turns=4" in result.error

    async def test_error_branch_surfaces_tool_errors(
        self, pkm_vault_path, routing_result_success, transcript_path,
    ) -> None:
        """When max_turns fires AND tool errors were observed, both are surfaced."""
        loop_result = AgentLoopResult(
            text_parts=["partial"],
            files_written=[],
            turns_used=15,
            error="Agent hit turn limit (15 turns)",
            tool_errors=["MCP error: vault path not found"],
        )
        with patch(
            "audio_ingest.capture.run_agent_loop_streaming",
            new=AsyncMock(return_value=loop_result),
        ):
            result = await agent_capture_session(
                routing_result_success, transcript_path, pkm_vault_path,
            )

        assert result.success is False
        assert "Agent hit turn limit" in result.error
        assert "MCP error" in result.error

    async def test_capture_options_pass_max_turns_15(
        self, pkm_vault_path, routing_result_success, transcript_path,
    ) -> None:
        """Capture must run with a hard turn cap so a misbehaving agent can't spin."""
        captured = {}

        async def mock_query(prompt, options):
            captured["options"] = options
            yield make_assistant_message(
                tool_blocks=[(
                    "mcp__obsidian-pkm__vault_append",
                    {"path": "01-Projects/Automation/development/devlog.md", "content": "x"},
                )],
            )

        with (
            patch("agent_infra.runner.query", side_effect=mock_query),
            patch("agent_infra.runner.AssistantMessage", MockAssistantMessage),
            patch("agent_infra.runner.TextBlock", MockTextBlock),
            patch("agent_infra.runner.ToolUseBlock", MockToolUseBlock),
        ):
            await agent_capture_session(
                routing_result_success, transcript_path, pkm_vault_path,
            )

        assert captured["options"].max_turns == 15


# ---------------------------------------------------------------------------
# Pipeline wiring: (a) flag off, (b) no files, (c) extraction failed, (d) happy path
# ---------------------------------------------------------------------------


def _make_config(tmp_path, *, capture: bool = False) -> DaemonConfig:
    return DaemonConfig(
        telegram_bot_token="t",
        telegram_chat_id="c",
        pkm_vault_path=tmp_path / "pkm",
        enable_session_capture=capture,
    )


def _make_transcript() -> TranscriptData:
    return TranscriptData(
        job_id="cap-job",
        recorded_at="2026-04-26T12:00:00+00:00",
        duration_seconds=60.0,
        speakers=["Alice"],
        segments=[TranscriptSegment(start=0.0, end=5.0, speaker="Alice", text="hi")],
        full_text="[Alice] hi",
    )


def _make_job() -> RecordingJob:
    return RecordingJob(
        id="cap-job",
        recorded_at="2026-04-26T12:00:00+00:00",
        filename="Cap Test",
        source="plaud-email",
        transcript_data=_make_transcript(),
        duration_ms=60000,
    )


def _make_routing_success() -> AgentRoutingResult:
    return AgentRoutingResult(
        success=True,
        summary="Routed",
        files_written=["00-Inbox/audio-ingestion/2026-04-26-cap.md"],
        summary_path="00-Inbox/audio-ingestion/2026-04-26-cap.md",
        turns_used=4,
    )


class TestPipelineCaptureWiring:
    @pytest.mark.asyncio
    async def test_a_flag_off_no_capture(self, tmp_path) -> None:
        from audio_ingest.pipeline import process_recording

        config = _make_config(tmp_path, capture=False)
        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=None),
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=Path("/tmp/t.md")),
            patch(
                "audio_ingest.pipeline.agent_extract_and_route",
                new_callable=AsyncMock, return_value=_make_routing_success(),
            ),
            patch("audio_ingest.pipeline.send_routing_summary", new_callable=AsyncMock),
            patch("audio_ingest.pipeline.agent_capture_session", new_callable=AsyncMock) as mock_cap,
        ):
            await process_recording(_make_job(), config, status=AsyncMock())

        mock_cap.assert_not_called()

    @pytest.mark.asyncio
    async def test_b_no_files_written_no_capture(self, tmp_path) -> None:
        from audio_ingest.pipeline import process_recording

        config = _make_config(tmp_path, capture=True)
        empty_routing = AgentRoutingResult(
            success=False, summary="", files_written=[], error="no files",
        )
        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=None),
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=Path("/tmp/t.md")),
            patch(
                "audio_ingest.pipeline.agent_extract_and_route",
                new_callable=AsyncMock, return_value=empty_routing,
            ),
            patch("audio_ingest.pipeline.send_routing_summary", new_callable=AsyncMock),
            patch("audio_ingest.pipeline.agent_capture_session", new_callable=AsyncMock) as mock_cap,
        ):
            await process_recording(_make_job(), config, status=AsyncMock())

        mock_cap.assert_not_called()

    @pytest.mark.asyncio
    async def test_c_extraction_failed_no_capture(self, tmp_path) -> None:
        from audio_ingest.pipeline import process_recording

        config = _make_config(tmp_path, capture=True)
        # Even with files written, success=False must skip capture
        failed_routing = AgentRoutingResult(
            success=False, summary="oops",
            files_written=["00-Inbox/audio-ingestion/x.md"],
            error="something failed",
        )
        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=None),
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=Path("/tmp/t.md")),
            patch(
                "audio_ingest.pipeline.agent_extract_and_route",
                new_callable=AsyncMock, return_value=failed_routing,
            ),
            patch("audio_ingest.pipeline.send_routing_summary", new_callable=AsyncMock),
            patch("audio_ingest.pipeline.agent_capture_session", new_callable=AsyncMock) as mock_cap,
        ):
            await process_recording(_make_job(), config, status=AsyncMock())

        mock_cap.assert_not_called()

    @pytest.mark.asyncio
    async def test_d_flag_on_and_success_runs_capture(self, tmp_path) -> None:
        from audio_ingest.pipeline import process_recording

        config = _make_config(tmp_path, capture=True)
        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=None),
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=Path("/tmp/t.md")),
            patch(
                "audio_ingest.pipeline.agent_extract_and_route",
                new_callable=AsyncMock, return_value=_make_routing_success(),
            ),
            patch("audio_ingest.pipeline.send_routing_summary", new_callable=AsyncMock),
            patch("audio_ingest.pipeline.agent_capture_session", new_callable=AsyncMock) as mock_cap,
        ):
            await process_recording(_make_job(), config, status=AsyncMock())

        mock_cap.assert_called_once()

    @pytest.mark.asyncio
    async def test_capture_returning_unsuccess_logs_warning(
        self, tmp_path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When capture returns success=False, pipeline logs a warning."""
        from audio_ingest.pipeline import process_recording

        config = _make_config(tmp_path, capture=True)
        unsuccess = CaptureResult(
            success=False, summary="", error="no devlog appended", turns_used=2,
        )
        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=None),
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=Path("/tmp/t.md")),
            patch(
                "audio_ingest.pipeline.agent_extract_and_route",
                new_callable=AsyncMock, return_value=_make_routing_success(),
            ),
            patch("audio_ingest.pipeline.send_routing_summary", new_callable=AsyncMock),
            patch(
                "audio_ingest.pipeline.agent_capture_session",
                new_callable=AsyncMock, return_value=unsuccess,
            ),
            caplog.at_level(logging.WARNING, logger="audio_ingest.pipeline"),
        ):
            await process_recording(_make_job(), config, status=AsyncMock())

        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "did not append devlog" in r.getMessage()
        ]
        assert warnings, (
            f"expected a warning containing 'did not append devlog'; "
            f"got records: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_capture_failure_does_not_break_pipeline(self, tmp_path) -> None:
        """A capture exception must be swallowed and logged, never propagated."""
        from audio_ingest.pipeline import process_recording

        config = _make_config(tmp_path, capture=True)
        status = AsyncMock()
        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=None),
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=Path("/tmp/t.md")),
            patch(
                "audio_ingest.pipeline.agent_extract_and_route",
                new_callable=AsyncMock, return_value=_make_routing_success(),
            ),
            patch("audio_ingest.pipeline.send_routing_summary", new_callable=AsyncMock),
            patch(
                "audio_ingest.pipeline.agent_capture_session",
                new_callable=AsyncMock, side_effect=RuntimeError("capture broke"),
            ),
        ):
            # Should not raise
            await process_recording(_make_job(), config, status=status)

        last_call = status.update.call_args_list[-1]
        assert last_call.args[0] == "completed"


# ---------------------------------------------------------------------------
# Config: enable_session_capture defaults
# ---------------------------------------------------------------------------


class TestSessionCaptureConfig:
    def test_default_off(self) -> None:
        cfg = DaemonConfig(
            telegram_bot_token="t", telegram_chat_id="c", pkm_vault_path=Path("/tmp/v"),
        )
        assert cfg.enable_session_capture is False

    def test_from_env_default_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
        monkeypatch.setenv("PKM_VAULT_PATH", "/tmp/vault")
        monkeypatch.delenv("ENABLE_SESSION_CAPTURE", raising=False)
        cfg = DaemonConfig.from_env()
        assert cfg.enable_session_capture is False

    def test_from_env_can_be_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
        monkeypatch.setenv("PKM_VAULT_PATH", "/tmp/vault")
        monkeypatch.setenv("ENABLE_SESSION_CAPTURE", "true")
        cfg = DaemonConfig.from_env()
        assert cfg.enable_session_capture is True
