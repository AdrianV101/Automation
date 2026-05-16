from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


def iso_week_key(d: date) -> str:
    """ISO-week key 'YYYY-WW' (ISO year + zero-padded ISO week number).

    Uses date.isocalendar(): the ISO year can differ from d.year near
    Jan 1 / Dec 31 (e.g. 2027-01-01 is ISO 2026-W53).
    """
    iso = d.isocalendar()
    return f"{iso.year:04d}-{iso.week:02d}"


def iso_week_dates(week_key: str) -> list[date]:
    """The 7 dates (Mon..Sun) of the given ISO-week key, ascending."""
    year_s, week_s = week_key.split("-", 1)
    year, week = int(year_s), int(week_s)
    monday = date.fromisocalendar(year, week, 1)
    return [monday + timedelta(days=i) for i in range(7)]


def compute_target_iso_week(now: datetime) -> str:
    """The just-closed ISO week as of `now`.

    The scheduler fires Sunday evening. Sunday is the LAST day of its own
    ISO week, so the week that just closed is simply the ISO week
    containing `now`'s local date.
    """
    return iso_week_key(now.date())


def seconds_until_next_weekly_fire(
    now: datetime, *, weekday: int, fire_time: time, tz: ZoneInfo,
) -> float:
    """Seconds until the next `weekday`@`fire_time` in `tz`.

    `weekday` uses date.weekday() convention (Mon=0..Sun=6). Endpoints are
    converted to UTC before subtraction so the result is a true physical
    duration across DST transitions (a naive same-tz subtraction would be
    off by an hour on a transition weekend).
    """
    now_local = now.astimezone(tz)
    days_ahead = (weekday - now_local.weekday()) % 7
    candidate = now_local.replace(
        hour=fire_time.hour, minute=fire_time.minute,
        second=0, microsecond=0,
    ) + timedelta(days=days_ahead)
    if candidate <= now_local:
        candidate = candidate + timedelta(days=7)
    delta = (
        candidate.astimezone(timezone.utc)
        - now_local.astimezone(timezone.utc)
    )
    return delta.total_seconds()


def compute_backfill_weeks(
    *, target_week: str, window_weeks: int,
) -> list[str]:
    """ISO-week keys strictly older than `target_week`, within the window.

    Returns the `window_weeks` keys immediately preceding `target_week`
    (ascending). `target_week` itself is excluded — the regular Sunday
    fire handles the just-closed week; backfill only covers older weeks
    missed while the daemon was down. ISO-year-boundary safe (steps by
    7-day date arithmetic, not naive week-number math). Returns [] for
    window_weeks <= 0.
    """
    if window_weeks <= 0:
        return []
    anchor_monday = iso_week_dates(target_week)[0]
    out: list[str] = []
    for i in range(window_weeks, 0, -1):
        wk = iso_week_key(anchor_monday - timedelta(weeks=i))
        out.append(wk)
    return out


import asyncio as _asyncio
from collections.abc import Awaitable as _Awaitable, Callable as _Callable
from dataclasses import dataclass
from typing import Protocol

RunForWeekFn = _Callable[[str], _Awaitable[None]]
NotifyFn = _Callable[[str], _Awaitable[None]]


class _WeeklyDB(Protocol):
    async def attempted_weeks(self, candidates: set[str]) -> set[str]: ...


@dataclass
class NewsWeeklyScheduler:
    """Independent weekly loop. NOT chained with the daily master.

    Mirrors NewsDailyScheduler: backfill once on boot, then
    sleep-fire-sleep forever. Each fire targets the just-closed ISO week.
    """
    db: _WeeklyDB
    run_for_week_fn: RunForWeekFn
    # Injected by the orchestrator and used by the runner for the weekly
    # success ping; reserved at the scheduler level (the weekly design
    # intentionally has no scheduler-side older-than-window alert, unlike
    # NewsDailyScheduler — see design D4, anti-Telegram-noise).
    notify: NotifyFn
    weekday: int
    fire_time: time
    tz: ZoneInfo
    backfill_window_weeks: int = 2

    async def run_backfill(self, *, now: datetime) -> None:
        target = compute_target_iso_week(now.astimezone(self.tz))
        candidates = compute_backfill_weeks(
            target_week=target, window_weeks=self.backfill_window_weeks,
        )
        if not candidates:
            return
        attempted = await self.db.attempted_weeks(set(candidates))
        for wk in candidates:  # ascending; bounded by window
            if wk in attempted:
                continue
            try:
                await self.run_for_week_fn(wk)
            except Exception:
                log.exception("Weekly backfill run for %s failed", wk)

    async def run_once(self, *, now: datetime) -> None:
        """One-shot fire — runs the ISO week that contains `now`.

        Unlike NewsDailyScheduler.run_once (which targets *yesterday*),
        the weekly target is the just-closed ISO week containing `now`
        (Sunday is the last day of its own ISO week).
        """
        target = compute_target_iso_week(now.astimezone(self.tz))
        await self.run_for_week_fn(target)

    async def run_forever(
        self, *, now_fn: _Callable[[], datetime] | None = None,
    ) -> None:
        """Main loop: backfill once, then sleep-fire-sleep forever.

        `now_fn` lets tests inject a clock — defaults to
        `datetime.now(self.tz)`.
        """
        clock = now_fn or (lambda: datetime.now(self.tz))
        await self.run_backfill(now=clock())
        while True:
            secs = seconds_until_next_weekly_fire(
                clock(), weekday=self.weekday,
                fire_time=self.fire_time, tz=self.tz,
            )
            log.info("News weekly scheduler sleeping %.0f s", secs)
            await _asyncio.sleep(secs)
            try:
                await self.run_once(now=clock())
            except Exception:
                log.exception("Scheduled news weekly run failed")
