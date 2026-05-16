from __future__ import annotations

from pathlib import Path

import pytest

from automation_daemon.news_weekly_patterns.state import (
    NEWS_WEEKLY_VALID_STATUSES,
    NewsWeeklyStateDB,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state.db"


@pytest.mark.asyncio
async def test_insert_then_get_run(db_path: Path) -> None:
    db = NewsWeeklyStateDB(db_path)
    await db.init_db()
    await db.insert_run("2026-20")
    row = await db.get_run("2026-20")
    assert row is not None
    assert row["status"] == "running"
    assert row["completed_at"] is None


@pytest.mark.asyncio
async def test_update_completed_sets_fields(db_path: Path) -> None:
    db = NewsWeeklyStateDB(db_path)
    await db.init_db()
    await db.insert_run("2026-20")
    await db.update_run(
        "2026-20", status="completed",
        threads_written=4, entities_enriched=4,
        cost_usd=0.31, turns_used=22,
    )
    row = await db.get_run("2026-20")
    assert row["status"] == "completed"
    assert row["threads_written"] == 4
    assert row["entities_enriched"] == 4
    assert row["cost_usd"] == pytest.approx(0.31)
    assert row["turns_used"] == 22
    assert row["completed_at"] is not None


@pytest.mark.asyncio
async def test_rejects_unknown_status(db_path: Path) -> None:
    db = NewsWeeklyStateDB(db_path)
    await db.init_db()
    await db.insert_run("2026-20")
    with pytest.raises(ValueError, match="invalid status"):
        await db.update_run("2026-20", status="bogus")


@pytest.mark.asyncio
async def test_rejects_unknown_column(db_path: Path) -> None:
    db = NewsWeeklyStateDB(db_path)
    await db.init_db()
    await db.insert_run("2026-20")
    with pytest.raises(ValueError, match="unknown columns"):
        await db.update_run("2026-20", status="completed", bogus=1)


@pytest.mark.asyncio
async def test_update_missing_row_raises_keyerror(db_path: Path) -> None:
    db = NewsWeeklyStateDB(db_path)
    await db.init_db()
    with pytest.raises(KeyError):
        await db.update_run("2026-99", status="completed")


@pytest.mark.asyncio
async def test_attempted_weeks_returns_inserted(db_path: Path) -> None:
    db = NewsWeeklyStateDB(db_path)
    await db.init_db()
    await db.insert_run("2026-18")
    await db.insert_run("2026-20")
    attempted = await db.attempted_weeks({"2026-18", "2026-19", "2026-20"})
    assert attempted == {"2026-18", "2026-20"}


def test_valid_statuses_frozenset() -> None:
    assert "skipped_insufficient_corpus" in NEWS_WEEKLY_VALID_STATUSES
    assert "completed" in NEWS_WEEKLY_VALID_STATUSES
