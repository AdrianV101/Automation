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
