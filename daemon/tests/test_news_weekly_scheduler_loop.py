from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from automation_daemon.news_weekly_patterns.scheduler import (
    NewsWeeklyScheduler,
)


class _FakeDB:
    def __init__(self, attempted: set[str]) -> None:
        self._attempted = attempted

    async def attempted_weeks(self, candidates: set[str]) -> set[str]:
        return self._attempted & candidates


@pytest.mark.asyncio
async def test_run_backfill_runs_only_unattempted_weeks() -> None:
    ran: list[str] = []

    async def run_for_week(wk: str) -> None:
        ran.append(wk)

    async def notify(_: str) -> None:
        pass

    sched = NewsWeeklyScheduler(
        db=_FakeDB(attempted={"2026-19"}),
        run_for_week_fn=run_for_week,
        notify=notify,
        weekday=6,
        fire_time=time(18, 0),
        tz=ZoneInfo("Europe/London"),
        backfill_window_weeks=2,
    )
    await sched.run_backfill(now=datetime(
        2026, 5, 17, 9, 0, tzinfo=ZoneInfo("Europe/London"),
    ))
    assert ran == ["2026-18"]


@pytest.mark.asyncio
async def test_run_once_runs_target_week() -> None:
    ran: list[str] = []

    async def run_for_week(wk: str) -> None:
        ran.append(wk)

    async def notify(_: str) -> None:
        pass

    sched = NewsWeeklyScheduler(
        db=_FakeDB(attempted=set()),
        run_for_week_fn=run_for_week,
        notify=notify,
        weekday=6,
        fire_time=time(18, 0),
        tz=ZoneInfo("Europe/London"),
        backfill_window_weeks=2,
    )
    await sched.run_once(now=datetime(
        2026, 5, 17, 18, 0, tzinfo=ZoneInfo("Europe/London"),
    ))
    assert ran == ["2026-20"]


@pytest.mark.asyncio
async def test_backfill_swallows_per_week_exceptions() -> None:
    ran: list[str] = []

    async def run_for_week(wk: str) -> None:
        ran.append(wk)
        raise RuntimeError("boom")

    async def notify(_: str) -> None:
        pass

    sched = NewsWeeklyScheduler(
        db=_FakeDB(attempted=set()),
        run_for_week_fn=run_for_week,
        notify=notify,
        weekday=6,
        fire_time=time(18, 0),
        tz=ZoneInfo("Europe/London"),
        backfill_window_weeks=1,
    )
    # Must not raise — a failing backfill week is logged, not fatal.
    await sched.run_backfill(now=datetime(
        2026, 5, 17, 9, 0, tzinfo=ZoneInfo("Europe/London"),
    ))
    # Proves the week WAS attempted and the exception was swallowed
    # (not skipped before the call). target=2026-20, window=1 -> 2026-19.
    assert ran == ["2026-19"]


@pytest.mark.asyncio
async def test_run_forever_backfill_error_is_non_fatal() -> None:
    import asyncio

    class _BoomDB:
        async def attempted_weeks(self, candidates: set[str]) -> set[str]:
            raise RuntimeError("state db down")

    async def run_for_week(wk: str) -> None:
        pass

    async def notify(_: str) -> None:
        pass

    sched = NewsWeeklyScheduler(
        db=_BoomDB(),
        run_for_week_fn=run_for_week,
        notify=notify,
        weekday=6,
        fire_time=time(18, 0),
        tz=ZoneInfo("Europe/London"),
        backfill_window_weeks=2,
    )
    now = datetime(2026, 5, 17, 9, 0, tzinfo=ZoneInfo("Europe/London"))
    task = asyncio.create_task(sched.run_forever(now_fn=lambda: now))
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # Reaching here without the RuntimeError propagating proves the
    # backfill error was caught and the loop proceeded to the sleep.
