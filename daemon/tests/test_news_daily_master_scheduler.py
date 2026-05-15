"""Tests for news_daily_master.scheduler."""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from automation_daemon.news_daily_master.scheduler import (
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
        """London springs forward 2026-03-29 01:00 GMT -> 02:00 BST.

        now = 2026-03-28 07:00 GMT, next 06:00 fire = 2026-03-29 06:00 BST
        = 05:00 UTC. The physical gap is 22h (the night lost an hour); a
        naive wall-clock subtraction would wrongly return 23h.
        """
        tz = ZoneInfo("Europe/London")
        now = datetime(2026, 3, 28, 7, 0, tzinfo=tz)  # 7am day before DST
        secs = seconds_until_next_fire(now, fire_time=time(6, 0), tz=tz)
        assert secs == pytest.approx(22 * 3600, abs=1)

    def test_handles_dst_fall_back(self) -> None:
        """London falls back 2026-10-25 02:00 BST -> 01:00 GMT.

        now = 2026-10-24 07:00 BST (= 06:00 UTC), next 06:00 fire =
        2026-10-25 06:00 GMT (= 06:00 UTC). The physical gap is 24h (the
        night gained an hour); a naive wall-clock subtraction would
        wrongly return 23h.
        """
        tz = ZoneInfo("Europe/London")
        now = datetime(2026, 10, 24, 7, 0, tzinfo=tz)  # day before fold
        secs = seconds_until_next_fire(now, fire_time=time(6, 0), tz=tz)
        assert secs == pytest.approx(24 * 3600, abs=1)

    def test_returns_positive_float(self) -> None:
        tz = ZoneInfo("UTC")
        now = datetime(2026, 4, 30, 5, 59, 30, tzinfo=tz)
        secs = seconds_until_next_fire(now, fire_time=time(6, 0), tz=tz)
        assert isinstance(secs, float)
        assert secs > 0


from automation_daemon.news_daily_master.scheduler import compute_backfill_dates


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
        from automation_daemon.news_daily_master.scheduler import (
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
        from automation_daemon.news_daily_master.scheduler import (
            compute_older_than_window,
        )
        today = date(2026, 4, 30)
        out = compute_older_than_window(
            last_completed=date(2026, 4, 28), today=today, window_days=3,
        )
        assert out == []


# ---------------------------------------------------------------------------
# NewsDailyScheduler tests
# ---------------------------------------------------------------------------

import asyncio
from unittest.mock import AsyncMock

from automation_daemon.news_daily_master.scheduler import NewsDailyScheduler
from automation_daemon.news_daily_master.state import NewsDailyMasterStateDB


@pytest.fixture
def state_db_path(tmp_path):
    return tmp_path / "state.db"


@pytest.mark.asyncio
async def test_scheduler_run_once_executes_yesterday(
    monkeypatch: pytest.MonkeyPatch, state_db_path,
) -> None:
    """Manual single-shot helper: runs `run_for_date(yesterday)` once."""
    db = NewsDailyMasterStateDB(state_db_path)
    await db.init_db()

    runs: list[date] = []
    async def fake_run(d: date) -> None:
        runs.append(d)

    sched = NewsDailyScheduler(
        db=db, run_for_date_fn=fake_run,
        notify=AsyncMock(),
        fire_time=time(6, 0), tz=ZoneInfo("UTC"),
        backfill_window_days=3,
    )

    fixed_now = datetime(2026, 4, 30, 6, 1, tzinfo=ZoneInfo("UTC"))
    await sched.run_once(now=fixed_now)
    assert runs == [date(2026, 4, 29)]


@pytest.mark.asyncio
async def test_scheduler_backfill_runs_missing_dates_in_order(
    state_db_path,
) -> None:
    db = NewsDailyMasterStateDB(state_db_path)
    await db.init_db()

    runs: list[date] = []
    async def fake_run(d: date) -> None:
        runs.append(d)
        await db.insert_run(d)
        await db.update_run(d, status="completed", master_path=str(d))

    sched = NewsDailyScheduler(
        db=db, run_for_date_fn=fake_run,
        notify=AsyncMock(),
        fire_time=time(6, 0), tz=ZoneInfo("UTC"),
        backfill_window_days=3,
    )

    fixed_now = datetime(2026, 4, 30, 6, 1, tzinfo=ZoneInfo("UTC"))
    await sched.run_backfill(now=fixed_now)
    # Cold start: backfill 27, 28, 29 (window=3), in ascending order.
    assert runs == [date(2026, 4, 27), date(2026, 4, 28), date(2026, 4, 29)]


@pytest.mark.asyncio
async def test_scheduler_backfill_emits_older_than_window_notice(
    state_db_path,
) -> None:
    db = NewsDailyMasterStateDB(state_db_path)
    await db.init_db()
    # last_completed = 2026-04-20 — 9 days behind today (2026-04-30).
    await db.insert_run(date(2026, 4, 20))
    await db.update_run(
        date(2026, 4, 20), status="completed", master_path="x",
    )

    runs: list[date] = []
    async def fake_run(d: date) -> None:
        runs.append(d)
        await db.insert_run(d)
        await db.update_run(d, status="completed", master_path=str(d))

    notifier = AsyncMock()
    sched = NewsDailyScheduler(
        db=db, run_for_date_fn=fake_run, notify=notifier,
        fire_time=time(6, 0), tz=ZoneInfo("UTC"),
        backfill_window_days=3,
    )

    fixed_now = datetime(2026, 4, 30, 6, 1, tzinfo=ZoneInfo("UTC"))
    await sched.run_backfill(now=fixed_now)

    # Backfilled within window: 27, 28, 29 (3 days).
    assert runs == [date(2026, 4, 27), date(2026, 4, 28), date(2026, 4, 29)]
    # Notifier called once with the older-than-window list.
    notifier.assert_awaited_once()
    msg = notifier.await_args.args[0]
    assert "2026-04-21" in msg
    assert "2026-04-26" in msg


@pytest.mark.asyncio
async def test_scheduler_backfill_skips_terminally_failed_dates(
    state_db_path,
) -> None:
    """Regression test: terminally-failed dates must NOT be re-fired on every
    daemon restart. The 06:00 scheduled fire targets 'yesterday' regardless of
    state and will retry-once-per-day; backfill is for catching up on dates
    that have NEVER been attempted.
    """
    db = NewsDailyMasterStateDB(state_db_path)
    await db.init_db()
    # 2026-04-28 was attempted and terminally failed — should NOT be retried.
    await db.insert_run(date(2026, 4, 28))
    await db.update_run(
        date(2026, 4, 28), status="failed", error="model timeout 3x",
    )
    # 2026-04-27 completed cleanly.
    await db.insert_run(date(2026, 4, 27))
    await db.update_run(
        date(2026, 4, 27), status="completed", master_path="x",
    )
    # 2026-04-29 (yesterday) has never been tried.

    runs: list[date] = []
    async def fake_run(d: date) -> None:
        runs.append(d)

    sched = NewsDailyScheduler(
        db=db, run_for_date_fn=fake_run, notify=AsyncMock(),
        fire_time=time(6, 0), tz=ZoneInfo("UTC"),
        backfill_window_days=3,
    )

    fixed_now = datetime(2026, 4, 30, 6, 1, tzinfo=ZoneInfo("UTC"))
    await sched.run_backfill(now=fixed_now)
    # Only 04-29 (never attempted) — NOT 04-28 (terminally failed).
    assert runs == [date(2026, 4, 29)]
