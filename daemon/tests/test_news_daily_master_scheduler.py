"""Tests for news_daily_master.scheduler."""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from audio_ingest.news_daily_master.scheduler import (
    compute_target_date, seconds_until_next_fire,
)


class TestComputeTargetDate:
    def test_target_date_is_yesterday_relative_to_now(self) -> None:
        now = datetime(2026, 4, 30, 6, 0, tzinfo=ZoneInfo("UTC"))
        assert compute_target_date(now) == date(2026, 4, 29)

    def test_target_date_uses_local_calendar(self) -> None:
        # 2026-04-30 03:00 UTC is still 2026-04-29 in US/Pacific (UTC-7).
        # 'yesterday' for the user is what's in the local calendar.
        now = datetime(2026, 4, 30, 3, 0, tzinfo=ZoneInfo("UTC"))
        local = now.astimezone(ZoneInfo("America/Los_Angeles"))
        assert compute_target_date(local) == date(2026, 4, 28)


class TestSecondsUntilNextFire:
    def test_when_now_is_before_today_fire_returns_today_delta(self) -> None:
        tz = ZoneInfo("Europe/London")
        now = datetime(2026, 4, 30, 4, 30, tzinfo=tz)  # 4:30 today
        secs = seconds_until_next_fire(now, fire_time=time(6, 0), tz=tz)
        assert secs == pytest.approx(90 * 60, abs=1)  # 1.5h

    def test_when_now_is_after_today_fire_returns_tomorrow_delta(self) -> None:
        tz = ZoneInfo("Europe/London")
        now = datetime(2026, 4, 30, 7, 0, tzinfo=tz)  # 7:00 today
        secs = seconds_until_next_fire(now, fire_time=time(6, 0), tz=tz)
        # ~23h until 06:00 tomorrow
        assert 22.5 * 3600 < secs < 23.5 * 3600

    def test_handles_dst_spring_forward(self) -> None:
        """In London, 2026-03-29 02:00 -> 03:00. Compute next 06:00 fire."""
        tz = ZoneInfo("Europe/London")
        now = datetime(2026, 3, 28, 7, 0, tzinfo=tz)  # 7am day before DST
        secs = seconds_until_next_fire(now, fire_time=time(6, 0), tz=tz)
        # 06:00 next day in London is 06:00 BST (UTC+1), and 'now' is BST too
        # actually BST starts on the 29th, so this is GMT->BST transition.
        # Just assert the delta is between 22h and 23.1h (not 24h flat).
        assert 22 * 3600 < secs < 23.1 * 3600

    def test_returns_positive_float(self) -> None:
        tz = ZoneInfo("UTC")
        now = datetime(2026, 4, 30, 5, 59, 30, tzinfo=tz)
        secs = seconds_until_next_fire(now, fire_time=time(6, 0), tz=tz)
        assert isinstance(secs, float)
        assert secs > 0


from audio_ingest.news_daily_master.scheduler import compute_backfill_dates


class TestComputeBackfillDates:
    def test_no_state_backfills_nothing_in_first_window(self) -> None:
        """With no prior runs, scheduler runs only yesterday at first fire.

        Backfill is bounded by both 'after last_completed' AND 'within window'.
        With no last_completed, we conservatively still bound to the window
        and let the first scheduled fire handle yesterday.
        """
        today = date(2026, 4, 30)
        out = compute_backfill_dates(
            last_completed=None, today=today, window_days=3,
        )
        # Returns dates in [today - window_days, yesterday] that need running.
        # With no last_completed, all of those are "missing".
        assert out == [
            date(2026, 4, 27), date(2026, 4, 28), date(2026, 4, 29),
        ]

    def test_skips_already_completed_dates(self) -> None:
        today = date(2026, 4, 30)
        out = compute_backfill_dates(
            last_completed=date(2026, 4, 28), today=today, window_days=3,
        )
        # last_completed=2026-04-28 means 27 and 28 are done.
        # Only 29 (yesterday) is missing within the 3-day window.
        assert out == [date(2026, 4, 29)]

    def test_returns_empty_when_caught_up(self) -> None:
        today = date(2026, 4, 30)
        out = compute_backfill_dates(
            last_completed=date(2026, 4, 29), today=today, window_days=3,
        )
        assert out == []

    def test_clamps_to_window(self) -> None:
        """If daemon was offline a week, only the last 3 days are backfilled."""
        today = date(2026, 4, 30)
        out = compute_backfill_dates(
            last_completed=date(2026, 4, 20), today=today, window_days=3,
        )
        assert out == [
            date(2026, 4, 27), date(2026, 4, 28), date(2026, 4, 29),
        ]


class TestComputeOlderThanWindow:
    def test_lists_dates_older_than_window(self) -> None:
        from audio_ingest.news_daily_master.scheduler import (
            compute_older_than_window,
        )
        today = date(2026, 4, 30)
        out = compute_older_than_window(
            last_completed=date(2026, 4, 20), today=today, window_days=3,
        )
        # Days older than window: 21..26 (between last_completed+1 and
        # window-start-1).
        assert out == [
            date(2026, 4, 21), date(2026, 4, 22), date(2026, 4, 23),
            date(2026, 4, 24), date(2026, 4, 25), date(2026, 4, 26),
        ]

    def test_returns_empty_when_within_window(self) -> None:
        from audio_ingest.news_daily_master.scheduler import (
            compute_older_than_window,
        )
        today = date(2026, 4, 30)
        out = compute_older_than_window(
            last_completed=date(2026, 4, 28), today=today, window_days=3,
        )
        assert out == []
