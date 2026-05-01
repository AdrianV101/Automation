from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pytest

from audio_ingest.hacker_news_adapter import _seconds_until_next_local_time


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
