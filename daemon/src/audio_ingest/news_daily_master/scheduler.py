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
