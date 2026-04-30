from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


def compute_target_date(now: datetime) -> date:
    """Return (now - 1 calendar day) in `now`'s tz/calendar.

    `now` is expected to be tz-aware. The 'yesterday' notion is the
    user's local calendar yesterday — we operate in `now`'s tz.
    """
    return (now - timedelta(days=1)).date()


def seconds_until_next_fire(
    now: datetime, fire_time: time, tz: ZoneInfo,
) -> float:
    """Seconds from `now` until the next occurrence of `fire_time` in `tz`.

    DST-aware: we compose the next fire as a local-naive datetime then
    `replace(tzinfo=tz)` so the wall-clock matches the user's expectation
    even across spring-forward / fall-back boundaries.
    """
    now_local = now.astimezone(tz)
    today_fire = now_local.replace(
        hour=fire_time.hour, minute=fire_time.minute,
        second=0, microsecond=0,
    )
    if today_fire <= now_local:
        next_fire = today_fire + timedelta(days=1)
    else:
        next_fire = today_fire
    delta = next_fire - now_local
    return delta.total_seconds()


def compute_backfill_dates(
    *, last_completed: date | None, today: date, window_days: int,
) -> list[date]:
    """Dates in [today - window_days, yesterday] that need a run.

    Excludes dates <= last_completed (already done). Returned ascending.
    """
    yesterday = today - timedelta(days=1)
    window_start = today - timedelta(days=window_days)
    cursor_start = window_start
    if last_completed is not None and last_completed >= window_start:
        cursor_start = last_completed + timedelta(days=1)
    if cursor_start > yesterday:
        return []
    out: list[date] = []
    d = cursor_start
    while d <= yesterday:
        out.append(d)
        d += timedelta(days=1)
    return out


def compute_older_than_window(
    *, last_completed: date | None, today: date, window_days: int,
) -> list[date]:
    """Dates between (last_completed+1) and window_start-1, exclusive of window.

    These are days that won't be backfilled (too old). Returned ascending.
    Empty if last_completed is None (no notion of 'newly missed' from cold start).
    """
    if last_completed is None:
        return []
    window_start = today - timedelta(days=window_days)
    cursor_start = last_completed + timedelta(days=1)
    cursor_end = window_start - timedelta(days=1)
    if cursor_start > cursor_end:
        return []
    out: list[date] = []
    d = cursor_start
    while d <= cursor_end:
        out.append(d)
        d += timedelta(days=1)
    return out


import asyncio as _asyncio
from collections.abc import Awaitable as _Awaitable, Callable as _Callable
from dataclasses import dataclass

from .state import NewsDailyMasterStateDB

RunForDateFn = _Callable[[date], _Awaitable[None]]
NotifyFn = _Callable[[str], _Awaitable[None]]


@dataclass
class NewsDailyScheduler:
    db: NewsDailyMasterStateDB
    run_for_date_fn: RunForDateFn
    notify: NotifyFn
    fire_time: time
    tz: ZoneInfo
    backfill_window_days: int = 3

    async def run_backfill(self, *, now: datetime) -> None:
        """One-shot: backfill missing days within the window, notify older."""
        today_local = now.astimezone(self.tz).date()
        last_completed = await self.db.get_last_completed()

        backfill = compute_backfill_dates(
            last_completed=last_completed,
            today=today_local,
            window_days=self.backfill_window_days,
        )
        # Sequentially — bounded by window size, no need for concurrency.
        for d in backfill:
            try:
                await self.run_for_date_fn(d)
            except Exception:
                log.exception("Backfill run for %s failed", d)

        older = compute_older_than_window(
            last_completed=last_completed,
            today=today_local,
            window_days=self.backfill_window_days,
        )
        if older:
            dates_str = ", ".join(d.isoformat() for d in older)
            try:
                await self.notify(
                    "⚠️ News daily master skipped backfill for "
                    f"{len(older)} day(s) older than the "
                    f"{self.backfill_window_days}-day window: {dates_str}.",
                )
            except Exception:
                log.exception("Failed to send older-than-window notice")

    async def run_once(self, *, now: datetime) -> None:
        """One-shot manual fire — runs (now - 1 day) regardless of cadence."""
        target = compute_target_date(now.astimezone(self.tz))
        await self.run_for_date_fn(target)

    async def run_forever(self, *, now_fn: _Callable[[], datetime] | None = None) -> None:
        """Main loop: backfill once, then sleep-fire-sleep forever.

        `now_fn` lets tests inject a clock — defaults to `datetime.now(tz)`.
        """
        clock = now_fn or (lambda: datetime.now(self.tz))
        await self.run_backfill(now=clock())
        while True:
            secs = seconds_until_next_fire(clock(), self.fire_time, self.tz)
            log.info("News daily scheduler sleeping %.0f s", secs)
            await _asyncio.sleep(secs)
            try:
                await self.run_once(now=clock())
            except Exception:
                log.exception("Scheduled news daily run failed")
