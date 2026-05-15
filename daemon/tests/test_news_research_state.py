from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from audio_ingest.news_research.state import (
    NEWS_RESEARCH_VALID_STATUSES,
    NewsResearchStateDB,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state.db"


@pytest.mark.asyncio
async def test_insert_then_get_run(db_path: Path) -> None:
    db = NewsResearchStateDB(db_path)
    await db.init_db()
    await db.insert_run(date(2026, 5, 15))
    row = await db.get_run(date(2026, 5, 15))
    assert row is not None
    assert row["status"] == "running"
    assert row["completed_at"] is None


@pytest.mark.asyncio
async def test_update_run_completed_sets_fields(db_path: Path) -> None:
    db = NewsResearchStateDB(db_path)
    await db.init_db()
    await db.insert_run(date(2026, 5, 15))
    await db.update_run(
        date(2026, 5, 15), status="completed",
        items_researched=3, cost_usd=0.42, turns_used=18,
    )
    row = await db.get_run(date(2026, 5, 15))
    assert row["status"] == "completed"
    assert row["items_researched"] == 3
    assert row["cost_usd"] == pytest.approx(0.42)
    assert row["turns_used"] == 18
    assert row["completed_at"] is not None


@pytest.mark.asyncio
async def test_update_run_rejects_unknown_status(db_path: Path) -> None:
    db = NewsResearchStateDB(db_path)
    await db.init_db()
    await db.insert_run(date(2026, 5, 15))
    with pytest.raises(ValueError, match="invalid status"):
        await db.update_run(date(2026, 5, 15), status="bogus")


@pytest.mark.asyncio
async def test_update_run_rejects_unknown_column(db_path: Path) -> None:
    db = NewsResearchStateDB(db_path)
    await db.init_db()
    await db.insert_run(date(2026, 5, 15))
    with pytest.raises(ValueError, match="unknown columns"):
        await db.update_run(
            date(2026, 5, 15), status="completed", bogus_col=1,
        )


@pytest.mark.asyncio
async def test_update_run_no_row_raises_keyerror(db_path: Path) -> None:
    db = NewsResearchStateDB(db_path)
    await db.init_db()
    with pytest.raises(KeyError):
        await db.update_run(date(2026, 5, 15), status="completed")


@pytest.mark.asyncio
async def test_insert_run_replaces_existing(db_path: Path) -> None:
    db = NewsResearchStateDB(db_path)
    await db.init_db()
    await db.insert_run(date(2026, 5, 15))
    await db.update_run(date(2026, 5, 15), status="failed", error="x")
    await db.insert_run(date(2026, 5, 15))  # re-run
    row = await db.get_run(date(2026, 5, 15))
    assert row["status"] == "running"
    assert row["error"] is None


def test_valid_statuses_frozenset() -> None:
    assert NEWS_RESEARCH_VALID_STATUSES == frozenset({
        "running", "completed", "skipped_no_master",
        "failed", "failed_notes_clobbered",
    })
