from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import AsyncMock

from automation_daemon.config import DaemonConfig
from automation_daemon.models import RecordingJob
from automation_daemon.speaker_reaper import reap_once
from automation_daemon.speaker_resolution import serialize_job
from email_ingest.state import EmailIngestStateDB
from pkm import TranscriptData, TranscriptSegment
from telegram_interface import BotConfig


def _job() -> RecordingJob:
    return RecordingJob(
        id="old", recorded_at="2026-05-15T10:00:00+00:00", filename="f",
        source="plaud-email",
        transcript_data=TranscriptData(
            "old", "2026-05-15T10:00:00+00:00", 1.0, ["Speaker 1"],
            [TranscriptSegment(0.0, 1.0, "Speaker 1", "hi")], "hi"),
        duration_ms=1000, source_metadata={},
    )


@pytest.mark.asyncio
async def test_reap_routes_stale_with_flag_and_marks_timed_out(tmp_path) -> None:
    db = EmailIngestStateDB(tmp_path / "s.db"); await db.init_db()
    await db.insert_event("old", uid=1)
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    await db.insert_pending("old", payload=serialize_job(_job()),
                            unknown_speakers=["Speaker 1"], created_at=old_ts)
    await db.update_status("old", "awaiting_speaker_labels")
    cfg = DaemonConfig(telegram_bot_token="t", telegram_chat_id="c", pkm_vault_path=tmp_path)
    bot = BotConfig(bot_token="t", chat_id="c")
    routed = AsyncMock()
    n = await reap_once(db, cfg, bot, ttl_hours=24, process_recording_fn=routed)
    assert n == 1
    routed.assert_awaited_once()
    assert routed.await_args.kwargs["speakers_unresolved"] is True
    # G1: row retained after timeout so a late reply can still correct it
    assert await db.get_pending("old") is not None
    assert (await db.get_event("old"))["status"] == "timed_out_unresolved"


@pytest.mark.asyncio
async def test_reap_skips_fresh_pending(tmp_path) -> None:
    db = EmailIngestStateDB(tmp_path / "s.db"); await db.init_db()
    await db.insert_event("new", uid=1)
    await db.insert_pending("new", payload=serialize_job(_job()),
                            unknown_speakers=["Speaker 1"])  # created now
    cfg = DaemonConfig(telegram_bot_token="t", telegram_chat_id="c", pkm_vault_path=tmp_path)
    bot = BotConfig(bot_token="t", chat_id="c")
    n = await reap_once(db, cfg, bot, ttl_hours=24, process_recording_fn=AsyncMock())
    assert n == 0
    assert await db.get_pending("new") is not None
