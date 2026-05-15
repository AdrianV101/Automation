"""24h timeout reaper for the speaker-resolution gate (ADR-010 §5).

Pending transcripts not labelled within `ttl_hours` are routed with their
generic Plaud labels and a `speakers_unresolved` frontmatter flag, then
moved to status `timed_out_unresolved`."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telegram_interface import BotConfig

from .config import DaemonConfig
from .pipeline import process_recording
from .speaker_resolution import deserialize_job
from email_ingest.state import EmailIngestStateDB, EmailIngestStatusTracker

log = logging.getLogger(__name__)


async def reap_once(
    email_db: EmailIngestStateDB,
    config: DaemonConfig,
    bot: BotConfig,
    *,
    ttl_hours: int = 24,
    process_recording_fn=process_recording,
) -> int:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    ).isoformat()
    stale = await email_db.list_pending_older_than(cutoff)
    for row in stale:
        mid = row["message_id"]
        try:
            job = deserialize_job(row["payload"])
            tracker = EmailIngestStatusTracker(email_db, mid)
            await process_recording_fn(
                job, config, status=tracker, speakers_unresolved=True,
            )
            await email_db.delete_pending(mid)
            await email_db.update_status(mid, "timed_out_unresolved")
        except Exception:
            log.exception("speaker reaper failed for %s", mid)
    return len(stale)


async def run_speaker_reaper_forever(
    email_db: EmailIngestStateDB,
    config: DaemonConfig,
    bot: BotConfig,
    *,
    ttl_hours: int = 24,
    interval_s: float = 3600.0,
) -> None:
    while True:
        try:
            await reap_once(email_db, config, bot, ttl_hours=ttl_hours)
        except Exception:
            log.exception("speaker reaper sweep crashed")
        await asyncio.sleep(interval_s)
