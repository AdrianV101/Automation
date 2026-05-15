"""Tests for news_daily_master.state.NewsDailyMasterStateDB."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from automation_daemon.news_daily_master.state import (
    NewsDailyMasterStateDB,
    NEWS_DAILY_MASTER_VALID_STATUSES,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_state.db"


@pytest.mark.asyncio
async def test_init_db_creates_table(db_path: Path) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    # Re-init must be idempotent.
    await db.init_db()


@pytest.mark.asyncio
async def test_insert_run_then_get_run(db_path: Path) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    await db.insert_run(target_date=date(2026, 4, 29))
    row = await db.get_run(date(2026, 4, 29))
    assert row is not None
    assert row["target_date"] == "2026-04-29"
    assert row["status"] == "running"
    assert row["started_at"] is not None
    assert row["completed_at"] is None
    assert row["master_path"] is None


@pytest.mark.asyncio
async def test_insert_run_duplicate_target_date_overwrites(db_path: Path) -> None:
    """Re-running for the same date is allowed — replaces the prior row."""
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    await db.insert_run(target_date=date(2026, 4, 29))
    # Second insert for same date — re-run scenario.
    await db.insert_run(target_date=date(2026, 4, 29))
    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "running"  # reset, not orphaned


def test_valid_statuses_constant() -> None:
    assert NEWS_DAILY_MASTER_VALID_STATUSES == frozenset({
        "running", "completed", "skipped_empty",
        "failed", "failed_verification", "failed_notes_clobbered",
    })


@pytest.mark.asyncio
async def test_update_run_completed_with_fields(db_path: Path) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    await db.insert_run(date(2026, 4, 29))
    await db.update_run(
        date(2026, 4, 29),
        status="completed",
        master_path="01-Projects/News/daily/2026-04-29-master.md",
        item_count=14,
    )
    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "completed"
    assert row["master_path"] == "01-Projects/News/daily/2026-04-29-master.md"
    assert row["item_count"] == 14
    assert row["completed_at"] is not None


@pytest.mark.asyncio
async def test_update_run_failed_records_error(db_path: Path) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    await db.insert_run(date(2026, 4, 29))
    await db.update_run(date(2026, 4, 29), status="failed", error="model timeout")
    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "failed"
    assert row["error"] == "model timeout"
    assert row["completed_at"] is not None


@pytest.mark.asyncio
async def test_update_run_invalid_status_raises(db_path: Path) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    await db.insert_run(date(2026, 4, 29))
    with pytest.raises(ValueError, match="invalid status"):
        await db.update_run(date(2026, 4, 29), status="bogus")


@pytest.mark.asyncio
async def test_update_run_unknown_field_raises(db_path: Path) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    await db.insert_run(date(2026, 4, 29))
    with pytest.raises(ValueError, match="unknown columns"):
        await db.update_run(date(2026, 4, 29), status="completed", bogus_field="x")


@pytest.mark.asyncio
async def test_update_run_missing_row_raises(db_path: Path) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    with pytest.raises(KeyError):
        await db.update_run(date(2026, 4, 29), status="completed")


@pytest.mark.asyncio
async def test_get_last_completed_none_when_empty(db_path: Path) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    assert await db.get_last_completed() is None


@pytest.mark.asyncio
async def test_get_last_completed_returns_most_recent(db_path: Path) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    for d in [date(2026, 4, 27), date(2026, 4, 28), date(2026, 4, 29)]:
        await db.insert_run(d)
        await db.update_run(d, status="completed", master_path=f"x/{d}.md")
    assert await db.get_last_completed() == date(2026, 4, 29)


@pytest.mark.asyncio
async def test_get_last_completed_ignores_non_completed(db_path: Path) -> None:
    """failed must not count as 'completed' for backfill purposes."""
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    await db.insert_run(date(2026, 4, 29))
    await db.update_run(date(2026, 4, 29), status="failed", error="x")
    assert await db.get_last_completed() is None


@pytest.mark.asyncio
async def test_get_last_completed_includes_skipped_empty(db_path: Path) -> None:
    """skipped_empty IS a successful 'nothing to do' — counts for backfill cursor."""
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    await db.insert_run(date(2026, 4, 29))
    await db.update_run(date(2026, 4, 29), status="skipped_empty")
    assert await db.get_last_completed() == date(2026, 4, 29)


@pytest.mark.asyncio
async def test_get_unattempted_in_window_returns_dates_with_no_row(db_path: Path) -> None:
    """Backfill semantics: a 'failed' row counts as ATTEMPTED — don't replay it.

    The scheduled 06:00 fire targets yesterday explicitly via run_once
    (INSERT OR REPLACE), so a failed-yesterday gets one retry per day from
    the schedule. Backfill is for catching up on dates the daemon never even
    saw, not for retrying terminal failures on every restart.
    """
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    # 04-27 completed.
    await db.insert_run(date(2026, 4, 27))
    await db.update_run(date(2026, 4, 27), status="completed", master_path="x")
    # 04-28 attempted but failed — has a row, so NOT in 'unattempted'.
    await db.insert_run(date(2026, 4, 28))
    await db.update_run(date(2026, 4, 28), status="failed", error="x")
    # 04-29 never attempted — IS in 'unattempted'.
    missing = await db.get_unattempted_in_window(
        start=date(2026, 4, 27), end=date(2026, 4, 29),
    )
    assert missing == [date(2026, 4, 29)]


@pytest.mark.asyncio
async def test_get_unattempted_in_window_handles_inverted_range(db_path: Path) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    out = await db.get_unattempted_in_window(
        start=date(2026, 4, 30), end=date(2026, 4, 27),
    )
    assert out == []
