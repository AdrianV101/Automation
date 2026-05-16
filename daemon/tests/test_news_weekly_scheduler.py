from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from automation_daemon.news_weekly_patterns.scheduler import (
    compute_backfill_weeks,
    compute_target_iso_week,
    iso_week_dates,
    iso_week_key,
    seconds_until_next_weekly_fire,
)


class TestIsoWeekKey:
    def test_key_format_zero_padded(self) -> None:
        assert iso_week_key(date(2026, 5, 13)) == "2026-20"

    def test_year_boundary_week_belongs_to_iso_year(self) -> None:
        assert iso_week_key(date(2027, 1, 1)) == "2026-53"


class TestIsoWeekDates:
    def test_returns_monday_to_sunday(self) -> None:
        days = iso_week_dates("2026-20")
        assert days[0] == date(2026, 5, 11)
        assert days[-1] == date(2026, 5, 17)
        assert len(days) == 7


class TestComputeTargetIsoWeek:
    def test_sunday_evening_targets_its_own_iso_week(self) -> None:
        now = datetime(2026, 5, 17, 18, 0, tzinfo=ZoneInfo("Europe/London"))
        assert compute_target_iso_week(now) == "2026-20"


class TestSecondsUntilNextWeeklyFire:
    def test_before_fire_same_weekday(self) -> None:
        tz = ZoneInfo("Europe/London")
        now = datetime(2026, 5, 17, 16, 0, tzinfo=tz)
        secs = seconds_until_next_weekly_fire(
            now, weekday=6, fire_time=time(18, 0), tz=tz,
        )
        assert secs == pytest.approx(2 * 3600, abs=1)

    def test_after_fire_rolls_to_next_week(self) -> None:
        tz = ZoneInfo("Europe/London")
        now = datetime(2026, 5, 17, 19, 0, tzinfo=tz)
        secs = seconds_until_next_weekly_fire(
            now, weekday=6, fire_time=time(18, 0), tz=tz,
        )
        assert (7 * 24 - 1.5) * 3600 < secs < (7 * 24 - 0.5) * 3600

    def test_midweek_advances_to_next_sunday(self) -> None:
        tz = ZoneInfo("Europe/London")
        now = datetime(2026, 5, 13, 12, 0, tzinfo=tz)
        secs = seconds_until_next_weekly_fire(
            now, weekday=6, fire_time=time(18, 0), tz=tz,
        )
        assert secs == pytest.approx(102 * 3600, abs=2)

    def test_dst_fall_back_week(self) -> None:
        tz = ZoneInfo("Europe/London")
        now = datetime(2026, 10, 23, 18, 0, tzinfo=tz)
        secs = seconds_until_next_weekly_fire(
            now, weekday=6, fire_time=time(18, 0), tz=tz,
        )
        assert secs == pytest.approx(49 * 3600, abs=2)


class TestComputeBackfillWeeks:
    def test_returns_unattempted_prior_weeks_excluding_current(self) -> None:
        weeks = compute_backfill_weeks(
            target_week="2026-20", window_weeks=2,
        )
        assert weeks == ["2026-18", "2026-19"]

    def test_year_boundary_backfill(self) -> None:
        weeks = compute_backfill_weeks(
            target_week="2027-02", window_weeks=3,
        )
        assert weeks == ["2026-52", "2026-53", "2027-01"]
