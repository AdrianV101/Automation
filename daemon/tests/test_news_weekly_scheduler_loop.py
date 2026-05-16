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
    async def run_for_week(wk: str) -> None:
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
    await sched.run_backfill(now=datetime(
        2026, 5, 17, 9, 0, tzinfo=ZoneInfo("Europe/London"),
    ))
