"""Tests for news_daily_master.state.NewsDailyMasterStateDB."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from audio_ingest.news_daily_master.state import (
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
