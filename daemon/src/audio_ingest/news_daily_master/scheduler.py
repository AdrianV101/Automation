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
