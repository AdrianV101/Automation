"""24h timeout reaper for the speaker-resolution gate (ADR-010 §5).

Pending transcripts not labelled within `ttl_hours` are routed with their
generic Plaud labels and a `speakers_unresolved` frontmatter flag, then
moved to status `timed_out_unresolved`."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telegram_interface import BotConfig, send_message

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
    max_attempts: int = 3,
    _failure_counts: dict[str, int] | None = None,
) -> int:
    """Route pending transcripts older than `ttl_hours` with generic labels
    + the `speakers_unresolved` flag, then stamp `timed_out_unresolved`.

    The pending row is RETAINED (ADR-010 §5: a late reply can still correct
    it) so this must be idempotent: a row already stamped
    `timed_out_unresolved` is skipped on subsequent sweeps. Returns the
    number of rows actually reaped this sweep (not the number scanned)."""
    counts = _failure_counts if _failure_counts is not None else {}
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    ).isoformat()
    stale = await email_db.list_pending_older_than(cutoff)
    reaped = 0
    for row in stale:
        mid = row["message_id"]
        event = await email_db.get_event(mid)
        if event is not None and event["status"] == "timed_out_unresolved":
            # Already reaped on a previous sweep; row is only retained so a
            # late reply can still correct it. Do not re-route.
            continue
        try:
            job = deserialize_job(row["payload"])
        except Exception:
            log.exception(
                "speaker reaper: unrecoverable pending payload for %s; "
                "marking failed and dropping the poisoned row", mid,
            )
            try:
                await email_db.update_status(mid, "failed")
            except Exception:
                log.exception("speaker reaper: could not mark %s failed", mid)
            await email_db.delete_pending(mid)
            continue
        try:
            tracker = EmailIngestStatusTracker(email_db, mid)
            await process_recording_fn(
                job, config, status=tracker, speakers_unresolved=True,
            )
        except Exception:
            counts[mid] = counts.get(mid, 0) + 1
            n = counts[mid]
            if n < max_attempts:
                log.exception(
                    "speaker reaper: routing failed for %s (attempt %d/%d); "
                    "leaving pending for next sweep", mid, n, max_attempts,
                )
                continue
            # max_attempts reached — escalate and give up
            log.exception(
                "speaker reaper: routing failed for %s %d times; giving up",
                mid, n,
            )
            try:
                await send_message(
                    f"⚠️ Speaker-gate: recording {mid} failed routing "
                    f"{n} times; giving up. Manual intervention needed.",
                    bot,
                )
            except Exception:
                log.exception(
                    "speaker reaper: could not send escalation alert for %s", mid,
                )
            try:
                await email_db.update_status(mid, "failed")
            except Exception:
                log.exception(
                    "speaker reaper: could not mark %s failed", mid,
                )
            await email_db.delete_pending(mid)
            continue
        counts.pop(mid, None)
        # A concurrent late reply may have resolved + deleted the pending
        # row while we were routing. _maybe_resume's clean run is
        # authoritative -- don't stamp timeout over it or count it.
        if await email_db.get_pending(mid) is None:
            log.info(
                "speaker reaper: %s resolved by a late reply mid-sweep; "
                "skipping timeout stamp", mid,
            )
            continue
        await email_db.update_status(mid, "timed_out_unresolved")
        reaped += 1
    return reaped


async def run_speaker_reaper_forever(
    email_db: EmailIngestStateDB,
    config: DaemonConfig,
    bot: BotConfig,
    *,
    ttl_hours: int = 24,
    interval_s: float = 3600.0,
) -> None:
    failure_counts: dict[str, int] = {}
    while True:
        try:
            await reap_once(
                email_db, config, bot,
                ttl_hours=ttl_hours,
                _failure_counts=failure_counts,
            )
        except Exception:
            log.exception("speaker reaper sweep crashed")
        await asyncio.sleep(interval_s)
