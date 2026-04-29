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

    def test_success_true_with_error_is_rejected(self) -> None:
        """Inconsistent success/error pair must fail at construction time.

        Without this, a misconstruction silently misleads operators reading
        `result.error` after a `success=True` -- type-design-analyzer I4.
        """
        with pytest.raises(ValueError, match="success=True.*error=None"):
            CaptureResult(success=True, summary="ok", error="actually failed")

    def test_success_false_without_error_is_rejected(self) -> None:
        """Failure must always carry an error message, not None."""
        with pytest.raises(ValueError, match="success=False.*non-None error"):
            CaptureResult(success=False, summary="failed")


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

    def test_excludes_codebase_and_web_tools(self) -> None:
        """Capture must not be able to read source code or fetch from the web.

        Without this, a refactor like `TOOLS_CAPTURE = TOOLS_EXTRACTION` (or any
        copy-paste from a broader profile) would silently expand the safety
        boundary. The vault_write/edit/admin exclusions above don't catch this.
        """
        for tool in ("Read", "Glob", "Grep", "WebSearch", "WebFetch"):
            assert tool not in TOOLS_CAPTURE, (
                f"{tool} must not be in TOOLS_CAPTURE -- capture is append-only "
                f"and has no business reading source or fetching from the web"
            )

    def test_capture_disjoint_from_forbidden(self) -> None:
        """Set-algebra spelling of the safety boundary.

        Composes the forbidden-set as `(write - {append, add_links}) | admin`
        and asserts disjointness. This is the negative invariant the type-
        design-analyzer (I6) identified as critical: a future profile change
        that introduced `vault_quick_append` into _VAULT_WRITE and re-derived
        TOOLS_CAPTURE differently would still need to clear this assertion.
        """
        from audio_ingest.tools import _VAULT_ADMIN, _VAULT_WRITE, VaultTool

        forbidden = (_VAULT_WRITE - {VaultTool.APPEND, VaultTool.ADD_LINKS}) | _VAULT_ADMIN
        assert TOOLS_CAPTURE.isdisjoint(forbidden), (
            f"TOOLS_CAPTURE leaks forbidden tools: {TOOLS_CAPTURE & forbidden}"
        )


class TestVaultToolPrefixInvariant:
    """The mcp__obsidian-pkm__ prefix is the SDK's match key. A typo'd
    member would silently fail to allowlist at runtime."""

    def test_every_vault_tool_has_prefix(self) -> None:
        from audio_ingest.tools import VAULT_PREFIX, VaultTool

        for member in VaultTool:
            assert member.value.startswith(VAULT_PREFIX), (
                f"{member.name} = {member.value!r} missing prefix {VAULT_PREFIX!r}"
            )

    def test_profiles_are_immutable(self) -> None:
        """The profiles are exported as frozenset so .add/.remove raise.

        Pre-I6, TOOLS_CAPTURE was a list -- any caller could
        `.append(VaultTool.WRITE.value)` and silently widen the safety
        boundary process-wide.
        """
        from audio_ingest.tools import (
            TOOLS_ASK, TOOLS_CAPTURE, TOOLS_COMMAND, TOOLS_EXTRACTION, TOOLS_TASK,
        )

        for profile in (TOOLS_CAPTURE, TOOLS_EXTRACTION, TOOLS_ASK, TOOLS_COMMAND, TOOLS_TASK):
            assert isinstance(profile, frozenset), (
                f"profile {profile!r} must be frozenset, got {type(profile)}"
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

    async def test_streams_when_tg_provided(
        self, pkm_vault_path, routing_result_success, transcript_path,
    ) -> None:
        """When tg config is provided, TelegramStreamSender is wired up.

        Mirrors test_extraction.py::test_streams_when_tg_provided so capture's
        Telegram tracing is regression-protected to the same standard.
        """
        from telegram_interface import BotConfig

        mock_result = AgentLoopResult(
            text_parts=["devlog updated"],
            files_written=["01-Projects/Automation/development/devlog.md"],
            turns_used=3,
        )

        async def mock_streaming(prompt, options, on_event=None):
            return mock_result

        with patch("audio_ingest.capture.run_agent_loop_streaming", side_effect=mock_streaming):
            with patch("audio_ingest.capture.TelegramStreamSender") as MockSender:
                mock_sender = MockSender.return_value
                mock_sender.handle = AsyncMock()
                mock_sender.flush = AsyncMock()

                tg = BotConfig(bot_token="fake", chat_id="123")
                result = await agent_capture_session(
                    routing_result_success, transcript_path, pkm_vault_path,
                    tg=tg, thread_id=42,
                )

                MockSender.assert_called_once_with(tg, 42)
                mock_sender.flush.assert_awaited_once()

        assert result.success

    async def test_no_streaming_without_tg(
        self, pkm_vault_path, routing_result_success, transcript_path,
    ) -> None:
        """When tg is None, no TelegramStreamSender is created and on_event=None."""
        mock_result = AgentLoopResult(
            text_parts=["devlog updated"],
            files_written=["01-Projects/Automation/development/devlog.md"],
            turns_used=3,
        )

        async def mock_streaming(prompt, options, on_event=None):
            assert on_event is None
            return mock_result

        with patch("audio_ingest.capture.run_agent_loop_streaming", side_effect=mock_streaming):
            result = await agent_capture_session(
                routing_result_success, transcript_path, pkm_vault_path,
            )

        assert result.success

    async def test_flush_failure_does_not_break_capture(
        self, pkm_vault_path, routing_result_success, transcript_path,
    ) -> None:
        """A Telegram flush failure must not regress the in-process capture
        result. Loses the trace tail; preserves the devlog append outcome."""
        from telegram_interface import BotConfig

        mock_result = AgentLoopResult(
            text_parts=["devlog updated"],
            files_written=["01-Projects/Automation/development/devlog.md"],
            turns_used=3,
        )

        async def mock_streaming(prompt, options, on_event=None):
            return mock_result

        with patch("audio_ingest.capture.run_agent_loop_streaming", side_effect=mock_streaming):
            with patch("audio_ingest.capture.TelegramStreamSender") as MockSender:
                mock_sender = MockSender.return_value
                mock_sender.handle = AsyncMock()
                mock_sender.flush = AsyncMock(side_effect=RuntimeError("telegram down"))

                tg = BotConfig(bot_token="fake", chat_id="123")
                result = await agent_capture_session(
                    routing_result_success, transcript_path, pkm_vault_path,
                    tg=tg, thread_id=42,
                )

        # Capture result is still success even though the trace flush blew up.
        assert result.success is True
        assert "01-Projects/Automation/development/devlog.md" in result.files_appended


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
    async def test_b1_success_false_no_capture(self, tmp_path) -> None:
        """success=False alone (with files_written non-empty) skips capture."""
        from audio_ingest.pipeline import process_recording

        config = _make_config(tmp_path, capture=True)
        success_false = AgentRoutingResult(
            success=False, summary="",
            files_written=["00-Inbox/audio-ingestion/x.md"],
            error="agent reported failure",
        )
        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=None),
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=Path("/tmp/t.md")),
            patch(
                "audio_ingest.pipeline.agent_extract_and_route",
                new_callable=AsyncMock, return_value=success_false,
            ),
            patch("audio_ingest.pipeline.send_routing_summary", new_callable=AsyncMock),
            patch("audio_ingest.pipeline.agent_capture_session", new_callable=AsyncMock) as mock_cap,
        ):
            await process_recording(_make_job(), config, status=AsyncMock())

        mock_cap.assert_not_called()

    @pytest.mark.asyncio
    async def test_b2_success_true_empty_files_no_capture(self, tmp_path) -> None:
        """success=True with empty files_written still skips capture.

        The pipeline gate trusts neither flag in isolation -- it AND-conjoins
        success and files_written so a hypothetical type-invariant violation
        (extraction.py is supposed to set success=False if files_written is
        empty) cannot accidentally fire capture against nothing.
        """
        from audio_ingest.pipeline import process_recording

        config = _make_config(tmp_path, capture=True)
        # Construct a deliberately-inconsistent result: success=True but no files.
        # This SHOULD NOT happen via agent_extract_and_route, but the gate should
        # be defensive against it.
        inconsistent = AgentRoutingResult(
            success=True, summary="claimed routing", files_written=[],
            summary_path=None,
        )
        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=None),
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=Path("/tmp/t.md")),
            patch(
                "audio_ingest.pipeline.agent_extract_and_route",
                new_callable=AsyncMock, return_value=inconsistent,
            ),
            patch("audio_ingest.pipeline.send_routing_summary", new_callable=AsyncMock),
            patch("audio_ingest.pipeline.agent_capture_session", new_callable=AsyncMock) as mock_cap,
        ):
            await process_recording(_make_job(), config, status=AsyncMock())

        mock_cap.assert_not_called()

    @pytest.mark.asyncio
    async def test_b3_routing_result_none_no_capture(self, tmp_path) -> None:
        """When agent_extract_and_route raises, pipeline rebinds routing_result
        to None. Capture must not fire against None."""
        from audio_ingest.pipeline import process_recording

        config = _make_config(tmp_path, capture=True)
        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=None),
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=Path("/tmp/t.md")),
            patch(
                "audio_ingest.pipeline.agent_extract_and_route",
                new_callable=AsyncMock, side_effect=RuntimeError("extraction blew up"),
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
        routing_result = _make_routing_success()
        transcript_path = Path("/tmp/t.md")
        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=None),
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=transcript_path),
            patch(
                "audio_ingest.pipeline.agent_extract_and_route",
                new_callable=AsyncMock, return_value=routing_result,
            ),
            patch("audio_ingest.pipeline.send_routing_summary", new_callable=AsyncMock),
            patch("audio_ingest.pipeline.agent_capture_session", new_callable=AsyncMock) as mock_cap,
        ):
            await process_recording(_make_job(), config, status=AsyncMock())

        mock_cap.assert_called_once()
        # Pin argument forwarding so a swap of routing_result/transcript_path/
        # vault_path (all dataclass-or-Path) doesn't pass type-check but breaks
        # the capture call. routing_result is identity-pinned; the rest are
        # checked structurally.
        call_args = mock_cap.call_args
        assert call_args.args[0] is routing_result, (
            "capture must receive the exact AgentRoutingResult from extraction"
        )
        assert call_args.args[1] == transcript_path, (
            "capture must receive transcript_path as its second positional arg"
        )
        assert call_args.args[2] == config.pkm_vault_path, (
            "capture must receive pkm_vault_path as its third positional arg"
        )
        # Telegram bot config and forum thread must be forwarded so the
        # capture trace mirrors to the same Telegram thread as extraction.
        assert "tg" in call_args.kwargs and call_args.kwargs["tg"] is not None
        assert "thread_id" in call_args.kwargs

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
            patch("audio_ingest.pipeline.send_message", new_callable=AsyncMock),
        ):
            # Should not raise
            await process_recording(_make_job(), config, status=status)

        last_call = status.update.call_args_list[-1]
        assert last_call.args[0] == "completed"

    @pytest.mark.asyncio
    async def test_capture_returning_unsuccess_notifies_telegram(self, tmp_path) -> None:
        """When capture returns success=False, the user must see a one-line
        notification in the same forum thread that has the routing summary.
        Without this, a user who opted into capture has no signal that the
        feature failed (silent-failure-hunter C2)."""
        from audio_ingest.pipeline import process_recording

        config = _make_config(tmp_path, capture=True)
        unsuccess = CaptureResult(
            success=False, summary="", error="no devlog appended (turns=4)",
            turns_used=4,
        )
        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=99),
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
            patch("audio_ingest.pipeline.send_message", new_callable=AsyncMock) as mock_send,
        ):
            await process_recording(_make_job(), config, status=AsyncMock())

        mock_send.assert_called_once()
        msg, _bot = mock_send.call_args.args[:2]
        assert "Devlog capture pass failed" in msg
        assert "no devlog appended" in msg
        assert mock_send.call_args.kwargs.get("thread_id") == 99

    @pytest.mark.asyncio
    async def test_capture_exception_notifies_telegram(self, tmp_path) -> None:
        """When capture raises, the user must still see a one-line
        notification (silent-failure-hunter C2)."""
        from audio_ingest.pipeline import process_recording

        config = _make_config(tmp_path, capture=True)
        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=99),
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=Path("/tmp/t.md")),
            patch(
                "audio_ingest.pipeline.agent_extract_and_route",
                new_callable=AsyncMock, return_value=_make_routing_success(),
            ),
            patch("audio_ingest.pipeline.send_routing_summary", new_callable=AsyncMock),
            patch(
                "audio_ingest.pipeline.agent_capture_session",
                new_callable=AsyncMock, side_effect=RuntimeError("kaboom"),
            ),
            patch("audio_ingest.pipeline.send_message", new_callable=AsyncMock) as mock_send,
        ):
            await process_recording(_make_job(), config, status=AsyncMock())

        mock_send.assert_called_once()
        msg = mock_send.call_args.args[0]
        assert "Devlog capture pass crashed" in msg

    @pytest.mark.asyncio
    async def test_config_error_propagates_to_outer_handler(self, tmp_path) -> None:
        """A daemon-wide config error (missing OBSIDIAN_MCP_SERVER_PATH, no
        node on PATH) must NOT be swallowed as a per-job capture failure.

        Pre-fix, the inner `except Exception` caught it; the operator only
        saw a per-job warning despite the underlying defect failing every
        recording. Post-fix, AgentInfraConfigError propagates to the outer
        pipeline handler which marks the job failed and sends a clearer
        Telegram error notification.
        """
        from agent_infra import AgentInfraConfigError
        from audio_ingest.pipeline import process_recording

        config = _make_config(tmp_path, capture=True)
        status = AsyncMock()
        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=99),
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=Path("/tmp/t.md")),
            patch(
                "audio_ingest.pipeline.agent_extract_and_route",
                new_callable=AsyncMock, return_value=_make_routing_success(),
            ),
            patch("audio_ingest.pipeline.send_routing_summary", new_callable=AsyncMock),
            patch(
                "audio_ingest.pipeline.agent_capture_session",
                new_callable=AsyncMock,
                side_effect=AgentInfraConfigError("OBSIDIAN_MCP_SERVER_PATH not set"),
            ),
            patch("audio_ingest.pipeline.send_transcription_error", new_callable=AsyncMock) as mock_err,
        ):
            await process_recording(_make_job(), config, status=status)

        # Outer handler ran: status -> failed, transcription error sent.
        last_call = status.update.call_args_list[-1]
        assert last_call.args[0] == "failed", (
            f"expected last status update to be 'failed' (config error escalated), "
            f"got {last_call.args}"
        )
        mock_err.assert_called_once()
        err_msg = mock_err.call_args.args[1]
        assert "OBSIDIAN_MCP_SERVER_PATH" in err_msg

    @pytest.mark.asyncio
    async def test_notify_failure_swallowed_does_not_break_pipeline(
        self, tmp_path,
    ) -> None:
        """If even the notification call fails, the pipeline still completes."""
        from audio_ingest.pipeline import process_recording

        config = _make_config(tmp_path, capture=True)
        status = AsyncMock()
        unsuccess = CaptureResult(
            success=False, summary="", error="no devlog", turns_used=1,
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
            patch(
                "audio_ingest.pipeline.send_message",
                new_callable=AsyncMock, side_effect=RuntimeError("telegram down"),
            ),
        ):
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
