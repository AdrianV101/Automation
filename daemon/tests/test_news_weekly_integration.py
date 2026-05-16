from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from automation_daemon.news_weekly_patterns.runner import (
    AgentRunInput,
    AgentRunOutput,
    RunnerConfig,
    run_for_iso_week,
)
from automation_daemon.news_weekly_patterns.scheduler import (
    NewsWeeklyScheduler,
)
from automation_daemon.news_weekly_patterns.state import NewsWeeklyStateDB

_ENTITY = "[[01-Projects/News/entities/Anthropic|Anthropic]]"


def _seed_week(root: Path) -> None:
    d = root / "01-Projects" / "News" / "daily"
    d.mkdir(parents=True, exist_ok=True)
    for day in range(11, 18):  # 2026-05-11 .. 17 == ISO 2026-20
        (d / f"2026-05-{day:02d}-master.md").write_text(
            f"## AI\n- story {_ENTITY}\n",
        )


@pytest.mark.asyncio
async def test_full_week_run_completes_and_preserves_notes(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _seed_week(vault)
    wk_dir = vault / "01-Projects" / "News" / "weekly"
    wk_dir.mkdir(parents=True, exist_ok=True)
    note = wk_dir / "2026-20.md"
    note.write_text("# old body\n\n## Notes\n\nhuman text DO NOT TOUCH\n")

    db = NewsWeeklyStateDB(tmp_path / "s.db")
    await db.init_db()

    async def agent(inp: AgentRunInput) -> AgentRunOutput:
        body = note.read_text()
        idx = body.index("## Notes")
        note.write_text(
            "# News Weekly Patterns — 2026-20\n\n"
            "## Anthropic\n\nthread narrative\n\n" + body[idx:],
        )
        return AgentRunOutput(
            success=True, threads_written=1, entities_enriched=1,
            cost_usd=0.1, turns_used=5,
        )

    pings: list[str] = []

    async def notify(m: str) -> None:
        pings.append(m)

    async def no_ratings(s: date, e: date) -> list[dict]:
        return []

    await run_for_iso_week(
        "2026-20", db=db,
        config=RunnerConfig(vault_root=vault, min_days=3),
        run_agent=agent, recent_ratings=no_ratings, notify=notify,
    )

    row = await db.get_run("2026-20")
    assert row["status"] == "completed"
    assert "human text DO NOT TOUCH" in note.read_text()
    assert "## Anthropic" in note.read_text()
    assert len(pings) == 1


@pytest.mark.asyncio
async def test_notes_clobber_detected(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_week(vault)
    wk_dir = vault / "01-Projects" / "News" / "weekly"
    wk_dir.mkdir(parents=True, exist_ok=True)
    note = wk_dir / "2026-20.md"
    note.write_text("# b\n\n## Notes\n\noriginal\n")

    db = NewsWeeklyStateDB(tmp_path / "s.db")
    await db.init_db()

    async def bad_agent(inp: AgentRunInput) -> AgentRunOutput:
        note.write_text("# b\n\n## Notes\n\nCLOBBERED\n")
        return AgentRunOutput(success=True, threads_written=1)

    async def notify(m: str) -> None:
        pass

    async def no_ratings(s: date, e: date) -> list[dict]:
        return []

    await run_for_iso_week(
        "2026-20", db=db,
        config=RunnerConfig(
            vault_root=vault, min_days=3,
            retry_backoff_seconds=(0.0,),
        ),
        run_agent=bad_agent, recent_ratings=no_ratings, notify=notify,
    )
    row = await db.get_run("2026-20")
    assert row["status"] == "failed_notes_clobbered"


@pytest.mark.asyncio
async def test_scheduler_run_once_drives_full_pass(
    tmp_path: Path,
) -> None:
    from datetime import datetime, time
    from zoneinfo import ZoneInfo

    vault = tmp_path / "vault"
    _seed_week(vault)
    db = NewsWeeklyStateDB(tmp_path / "s.db")
    await db.init_db()

    async def agent(inp: AgentRunInput) -> AgentRunOutput:
        return AgentRunOutput(success=True, threads_written=1)

    async def notify(m: str) -> None:
        pass

    async def no_ratings(s: date, e: date) -> list[dict]:
        return []

    async def run_for_week_fn(iso_week: str) -> None:
        await run_for_iso_week(
            iso_week, db=db,
            config=RunnerConfig(vault_root=vault, min_days=3),
            run_agent=agent, recent_ratings=no_ratings, notify=notify,
        )

    sched = NewsWeeklyScheduler(
        db=db, run_for_week_fn=run_for_week_fn, notify=notify,
        weekday=6, fire_time=time(18, 0), tz=ZoneInfo("Europe/London"),
        backfill_window_weeks=2,
    )
    await sched.run_once(now=datetime(
        2026, 5, 17, 18, 0, tzinfo=ZoneInfo("Europe/London"),
    ))
    assert (await db.get_run("2026-20"))["status"] == "completed"
