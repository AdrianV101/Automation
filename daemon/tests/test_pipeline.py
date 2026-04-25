from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from audio_ingest.extraction import AgentRoutingResult
from audio_ingest.config import DaemonConfig
from audio_ingest.models import RecordingJob
from pkm import TranscriptData, TranscriptSegment
from audio_ingest.pipeline import process_recording


def _make_config(tmp_path) -> DaemonConfig:
    return DaemonConfig(
        telegram_bot_token="test",
        telegram_chat_id="test",
        pkm_vault_path=tmp_path / "pkm",
    )


def _make_transcript_data(job_id: str = "test123") -> TranscriptData:
    return TranscriptData(
        job_id=job_id,
        recorded_at="2026-02-06T12:00:00+00:00",
        duration_seconds=60.0,
        speakers=["Alice"],
        segments=[TranscriptSegment(start=0.0, end=5.0, speaker="Alice", text="Hello")],
        full_text="[Alice] Hello",
    )


def _make_job() -> RecordingJob:
    return RecordingJob(
        id="test123",
        recorded_at="2026-02-06T12:00:00+00:00",
        filename="Test Recording",
        source="plaud-email",
        transcript_data=_make_transcript_data(),
        duration_ms=60000,
    )


def _make_routing_result(success: bool = True) -> AgentRoutingResult:
    if success:
        return AgentRoutingResult(
            success=True,
            summary="Routed items to PKM",
            files_written=[
                "00-Inbox/audio-ingestion/2026-02-06-test.md",
                "01-Projects/Automation/devlog.md",
            ],
            summary_path="00-Inbox/audio-ingestion/2026-02-06-test.md",
            turns_used=5,
        )
    return AgentRoutingResult(
        success=False,
        summary="",
        error="Agent completed but wrote no files",
        turns_used=3,
    )


def _make_status() -> AsyncMock:
    return AsyncMock()


class TestProcessRecording:
    @pytest.mark.asyncio
    async def test_full_pipeline_success(self, tmp_path):
        config = _make_config(tmp_path)
        job = _make_job()
        status = _make_status()
        routing_result = _make_routing_result(success=True)

        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=None),
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=Path("/tmp/transcript.md")) as mock_write_raw,
            patch("audio_ingest.pipeline.agent_extract_and_route", new_callable=AsyncMock, return_value=routing_result) as mock_route,
            patch("audio_ingest.pipeline.send_routing_summary", new_callable=AsyncMock) as mock_notify,
        ):
            await process_recording(job, config, status=status)

        status_calls = [call.args[0] for call in status.update.call_args_list]
        assert status_calls == ["writing_pkm", "extracting", "completed"]

        mock_write_raw.assert_called_once()
        # write_raw_transcript should be called with the TranscriptData directly
        assert mock_write_raw.call_args[0][0] is job.transcript_data

        mock_route.assert_called_once()
        # agent_extract_and_route should receive the TranscriptData as first arg
        assert mock_route.call_args[0][0] is job.transcript_data

        mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_raw_transcript_failure_marks_failed(self, tmp_path):
        # With TranscriptData now required on the job, the remaining failure
        # surface is write_raw_transcript raising.
        config = _make_config(tmp_path)
        job = _make_job()
        status = _make_status()

        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=None),
            patch("audio_ingest.pipeline.write_raw_transcript", side_effect=OSError("disk full")),
            patch("audio_ingest.pipeline.send_transcription_error", new_callable=AsyncMock) as mock_error,
        ):
            await process_recording(job, config, status=status)

        last_call = status.update.call_args_list[-1]
        assert last_call.args[0] == "failed"
        assert "disk full" in last_call.kwargs.get("error", "")
        mock_error.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_routing_failure_still_completes(self, tmp_path):
        """Transcript preserved even when agent routing fails."""
        config = _make_config(tmp_path)
        job = _make_job()
        status = _make_status()
        routing_result = _make_routing_result(success=False)

        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=None),
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=Path("/tmp/transcript.md")),
            patch("audio_ingest.pipeline.agent_extract_and_route", new_callable=AsyncMock, return_value=routing_result),
            patch("audio_ingest.pipeline.send_routing_summary", new_callable=AsyncMock),
        ):
            await process_recording(job, config, status=status)

        # Should still complete (transcript preserved)
        last_call = status.update.call_args_list[-1]
        assert last_call.args[0] == "completed"
        # Should include error info
        assert "Agent routing failed" in last_call.kwargs.get("error", "")

    @pytest.mark.asyncio
    async def test_agent_routing_exception_still_completes(self, tmp_path):
        """Pipeline completes even if agent_extract_and_route raises."""
        config = _make_config(tmp_path)
        job = _make_job()
        status = _make_status()

        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=None),
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=Path("/tmp/transcript.md")),
            patch("audio_ingest.pipeline.agent_extract_and_route", new_callable=AsyncMock, side_effect=RuntimeError("SDK crash")),
            patch("audio_ingest.pipeline.send_routing_summary", new_callable=AsyncMock),
        ):
            await process_recording(job, config, status=status)

        last_call = status.update.call_args_list[-1]
        assert last_call.args[0] == "completed"
        assert "Agent routing failed" in last_call.kwargs.get("error", "")

    @pytest.mark.asyncio
    async def test_telegram_failure_does_not_raise(self, tmp_path):
        """Pipeline should complete even if Telegram notification fails."""
        config = _make_config(tmp_path)
        job = _make_job()
        status = _make_status()
        routing_result = _make_routing_result(success=True)

        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=None),
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=Path("/tmp/transcript.md")),
            patch("audio_ingest.pipeline.agent_extract_and_route", new_callable=AsyncMock, return_value=routing_result),
            patch("audio_ingest.pipeline.send_routing_summary", new_callable=AsyncMock, side_effect=RuntimeError("Network error")),
        ):
            # Should not raise
            await process_recording(job, config, status=status)

        # Should still be marked completed
        last_call = status.update.call_args_list[-1]
        assert last_call.args[0] == "completed"


class TestPipelineRecordingTopic:
    @pytest.mark.asyncio
    async def test_creates_topic_and_passes_to_notifications(self, tmp_path):
        config = _make_config(tmp_path)
        job = _make_job()
        status = _make_status()
        routing_result = _make_routing_result(success=True)

        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=99) as mock_topic,
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=Path("/tmp/transcript.md")),
            patch("audio_ingest.pipeline.agent_extract_and_route", new_callable=AsyncMock, return_value=routing_result),
            patch("audio_ingest.pipeline.send_routing_summary", new_callable=AsyncMock) as mock_notify,
        ):
            await process_recording(job, config, status=status)

        # Topic should be created with recording date and filename
        mock_topic.assert_called_once()
        topic_name = mock_topic.call_args[0][0]
        assert "02/06" in topic_name
        assert "Test Recording" in topic_name

        # Routing summary should get the thread_id
        mock_notify.assert_called_once()
        assert mock_notify.call_args[1]["thread_id"] == 99

    @pytest.mark.asyncio
    async def test_topic_creation_failure_passes_none_thread_id(self, tmp_path):
        """Pipeline works even when topic creation fails (thread_id=None)."""
        config = _make_config(tmp_path)
        job = _make_job()
        status = _make_status()
        routing_result = _make_routing_result(success=True)

        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=None),
            patch("audio_ingest.pipeline.write_raw_transcript", return_value=Path("/tmp/transcript.md")),
            patch("audio_ingest.pipeline.agent_extract_and_route", new_callable=AsyncMock, return_value=routing_result),
            patch("audio_ingest.pipeline.send_routing_summary", new_callable=AsyncMock) as mock_notify,
        ):
            await process_recording(job, config, status=status)

        mock_notify.assert_called_once()
        assert mock_notify.call_args[1]["thread_id"] is None

        # Pipeline should still complete
        status_calls = [call.args[0] for call in status.update.call_args_list]
        assert "completed" in status_calls

    @pytest.mark.asyncio
    async def test_error_path_passes_thread_id_to_transcription_error(self, tmp_path):
        config = _make_config(tmp_path)
        job = _make_job()
        status = _make_status()

        with (
            patch("audio_ingest.pipeline.create_forum_topic", new_callable=AsyncMock, return_value=88),
            patch("audio_ingest.pipeline.write_raw_transcript", side_effect=OSError("disk full")),
            patch("audio_ingest.pipeline.send_transcription_error", new_callable=AsyncMock) as mock_error,
        ):
            await process_recording(job, config, status=status)

        mock_error.assert_called_once()
        assert mock_error.call_args[1]["thread_id"] == 88
