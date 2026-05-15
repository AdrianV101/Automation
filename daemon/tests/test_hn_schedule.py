from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pytest

from automation_daemon.hacker_news_adapter import _seconds_until_next_local_time


UTC = timezone.utc


def test_target_later_today_returns_seconds_to_today():
    tz = ZoneInfo("UTC")
    now = datetime(2026, 5, 1, 4, 0, 0, tzinfo=UTC)  # 04:00 UTC
    secs = _seconds_until_next_local_time(now=now, hour=5, minute=30, tz=tz)
    assert secs == int(timedelta(hours=1, minutes=30).total_seconds())


def test_target_already_passed_returns_seconds_to_tomorrow():
    tz = ZoneInfo("UTC")
    now = datetime(2026, 5, 1, 6, 0, 0, tzinfo=UTC)  # already past 05:30
    secs = _seconds_until_next_local_time(now=now, hour=5, minute=30, tz=tz)
    expected = int(timedelta(hours=23, minutes=30).total_seconds())
    assert secs == expected


def test_exact_match_returns_one_full_day():
    """If we wake exactly at the target second, schedule the next one."""
    tz = ZoneInfo("UTC")
    now = datetime(2026, 5, 1, 5, 30, 0, tzinfo=UTC)
    secs = _seconds_until_next_local_time(now=now, hour=5, minute=30, tz=tz)
    assert secs == 24 * 3600


def test_handles_local_timezone_offset():
    tz = ZoneInfo("Europe/London")  # BST = UTC+1 on this date
    # 04:00 UTC = 05:00 BST → 30 minutes until 05:30 BST
    now = datetime(2026, 5, 1, 4, 0, 0, tzinfo=UTC)
    secs = _seconds_until_next_local_time(now=now, hour=5, minute=30, tz=tz)
    assert secs == 30 * 60


def test_dst_fall_back_returns_physical_seconds():
    """Across the autumn fold the result is the true *physical* duration.

    Europe/London falls back on 2026-10-25: at 02:00 BST clocks go to
    01:00 GMT, so the night contains an extra repeated hour. With
    ``now`` = 00:30 UTC (= 01:30 BST, before the fold) and a 05:30 local
    target (= 05:30 GMT, after the fold), the wall-clock gap is 4h but the
    true elapsed physical time is 5h. ``asyncio.sleep`` consumes physical
    time, so the scheduler must sleep 5h to fire at 05:30 local — not 4h,
    which would fire an hour early.
    """
    tz = ZoneInfo("Europe/London")
    now = datetime(2026, 10, 25, 0, 30, 0, tzinfo=UTC)
    secs = _seconds_until_next_local_time(now=now, hour=5, minute=30, tz=tz)
    assert secs == 5 * 3600


def test_dst_spring_forward_returns_physical_seconds():
    """Across spring-forward the result is the true *physical* duration.

    Europe/London springs forward on 2026-03-29: at 01:00 GMT clocks jump
    to 02:00 BST, so the night loses an hour. With ``now`` = 00:30 UTC
    (= 00:30 GMT, before the jump) and a 05:30 local target (= 05:30 BST
    = 04:30 UTC, after the jump), the wall-clock gap is 5h but the true
    elapsed physical time is 4h. The scheduler must sleep the physical 4h
    so the digest fires at 05:30 BST, not an hour late.
    """
    tz = ZoneInfo("Europe/London")
    now = datetime(2026, 3, 29, 0, 30, 0, tzinfo=UTC)
    secs = _seconds_until_next_local_time(now=now, hour=5, minute=30, tz=tz)
    assert secs == 4 * 3600


def test_dst_spring_forward_imaginary_target_is_deterministic():
    """Characterisation: a target wall-clock time that does not exist.

    On 2026-03-29 Europe/London jumps 01:00 GMT -> 02:00 BST, so 01:30
    local never occurs. ZoneInfo resolves the imaginary time with the
    pre-transition offset (fold=0, i.e. GMT) -> 01:30 UTC, so from
    now=00:00 UTC the function returns 5400s (1h30m). Pinned so a future
    tz/fold-handling change is a conscious decision, not a silent shift.
    """
    tz = ZoneInfo("Europe/London")
    now = datetime(2026, 3, 29, 0, 0, 0, tzinfo=UTC)
    secs = _seconds_until_next_local_time(now=now, hour=1, minute=30, tz=tz)
    assert secs == 5400


def test_dst_fall_back_ambiguous_target_is_deterministic():
    """Characterisation: a target wall-clock time that occurs twice.

    On 2026-10-25 Europe/London falls back 02:00 BST -> 01:00 GMT, so
    01:30 local happens twice. ZoneInfo resolves the ambiguous time to
    the first occurrence (fold=0, BST: 01:30 - 01:00 = 00:30 UTC). From
    now=2026-10-24 23:30 UTC (= 00:30 BST on the 25th) the function
    returns 3600s (1h). Pinned to catch a silent fold-resolution change.
    """
    tz = ZoneInfo("Europe/London")
    now = datetime(2026, 10, 24, 23, 30, 0, tzinfo=UTC)
    secs = _seconds_until_next_local_time(now=now, hour=1, minute=30, tz=tz)
    assert secs == 3600
