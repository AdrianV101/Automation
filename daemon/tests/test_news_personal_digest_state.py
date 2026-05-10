"""Tests for news_personal_digest.state.NewsDigestStateDB."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from audio_ingest.news_personal_digest.state import (
    NewsDigestStateDB,
    NEWS_DIGEST_VALID_STATUSES,
    VALID_RATINGS,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_state.db"


@pytest.mark.asyncio
async def test_init_db_creates_tables(db_path: Path) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    # Re-init must be idempotent.
    await db.init_db()


@pytest.mark.asyncio
async def test_insert_run_then_get_run(db_path: Path) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    await db.insert_run(digest_date=date(2026, 5, 9))
    row = await db.get_run(date(2026, 5, 9))
    assert row is not None
    assert row["digest_date"] == "2026-05-09"
    assert row["status"] == "running"
    assert row["started_at"] is not None
    assert row["completed_at"] is None
    assert row["item_count"] is None


@pytest.mark.asyncio
async def test_insert_run_duplicate_overwrites(db_path: Path) -> None:
    """Re-running for the same date is allowed — replaces the prior row."""
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    await db.insert_run(digest_date=date(2026, 5, 9))
    await db.insert_run(digest_date=date(2026, 5, 9))
    row = await db.get_run(date(2026, 5, 9))
    assert row["status"] == "running"


def test_valid_statuses_constant() -> None:
    assert NEWS_DIGEST_VALID_STATUSES == frozenset({
        "running", "completed", "skipped_no_master",
        "failed", "failed_verification",
    })


def test_valid_ratings_constant() -> None:
    assert VALID_RATINGS == frozenset({
        "thumbs_up", "thumbs_down", "star",
    })


@pytest.mark.asyncio
async def test_update_run_to_completed(db_path: Path) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    await db.insert_run(digest_date=date(2026, 5, 9))
    await db.update_run(
        date(2026, 5, 9),
        status="completed",
        item_count=8,
        rating_signal_summary="Boosted AI items based on 3 recent ⭐.",
    )
    row = await db.get_run(date(2026, 5, 9))
    assert row["status"] == "completed"
    assert row["completed_at"] is not None
    assert row["item_count"] == 8
    assert row["rating_signal_summary"].startswith("Boosted AI")
    assert row["error"] is None


@pytest.mark.asyncio
async def test_update_run_to_failed_records_error(db_path: Path) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    await db.insert_run(digest_date=date(2026, 5, 9))
    await db.update_run(date(2026, 5, 9), status="failed", error="agent timeout")
    row = await db.get_run(date(2026, 5, 9))
    assert row["status"] == "failed"
    assert row["error"] == "agent timeout"
    assert row["completed_at"] is not None  # terminal status sets completed_at


@pytest.mark.asyncio
async def test_update_run_invalid_status_raises(db_path: Path) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    await db.insert_run(digest_date=date(2026, 5, 9))
    with pytest.raises(ValueError, match="invalid status"):
        await db.update_run(date(2026, 5, 9), status="bogus")


@pytest.mark.asyncio
async def test_update_run_unknown_column_raises(db_path: Path) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    await db.insert_run(digest_date=date(2026, 5, 9))
    with pytest.raises(ValueError, match="unknown columns"):
        await db.update_run(date(2026, 5, 9), status="completed", master_path="x")


@pytest.mark.asyncio
async def test_update_run_missing_row_raises(db_path: Path) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    with pytest.raises(KeyError):
        await db.update_run(date(2026, 5, 9), status="completed")


# ---------------------------------------------------------------------------
# news_digest_items
# ---------------------------------------------------------------------------

from audio_ingest.news_personal_digest.state import DigestItemInput  # noqa: E402


@pytest.mark.asyncio
async def test_insert_item_returns_autoincrement_id(db_path: Path) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    await db.insert_run(digest_date=date(2026, 5, 9))
    id1 = await db.insert_item(
        digest_date=date(2026, 5, 9),
        item=DigestItemInput(
            source_path="00-Inbox/news/2026-05-09/anthropic-x.md",
            category="AI",
            title="Anthropic ships Claude 4.7",
            url="https://anthropic.com/news/claude-4-7",
            position=1,
        ),
    )
    id2 = await db.insert_item(
        digest_date=date(2026, 5, 9),
        item=DigestItemInput(
            source_path="00-Inbox/news/2026-05-09/ecb-x.md",
            category="Finance",
            title="ECB holds rates",
            url=None,
            position=1,
        ),
    )
    assert id1 == 1
    assert id2 == 2


@pytest.mark.asyncio
async def test_get_digest_item_round_trip(db_path: Path) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    await db.insert_run(digest_date=date(2026, 5, 9))
    item_id = await db.insert_item(
        digest_date=date(2026, 5, 9),
        item=DigestItemInput(
            source_path="00-Inbox/news/2026-05-09/x.md",
            category="AI",
            title="X",
            url="https://x.example",
            position=1,
        ),
    )
    row = await db.get_digest_item(item_id)
    assert row is not None
    assert row["id"] == item_id
    assert row["digest_date"] == "2026-05-09"
    assert row["title"] == "X"
    assert row["telegram_message_id"] is None


@pytest.mark.asyncio
async def test_get_digest_item_missing_returns_none(db_path: Path) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    assert await db.get_digest_item(99999) is None


@pytest.mark.asyncio
async def test_attach_telegram_message_id(db_path: Path) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    await db.insert_run(digest_date=date(2026, 5, 9))
    id1 = await db.insert_item(
        digest_date=date(2026, 5, 9),
        item=DigestItemInput("p1", "AI", "T1", None, 1),
    )
    id2 = await db.insert_item(
        digest_date=date(2026, 5, 9),
        item=DigestItemInput("p2", "AI", "T2", None, 2),
    )
    id3 = await db.insert_item(
        digest_date=date(2026, 5, 9),
        item=DigestItemInput("p3", "Finance", "T3", None, 1),
    )
    await db.attach_telegram_message_id(item_ids=[id1, id2], telegram_message_id=10001)
    await db.attach_telegram_message_id(item_ids=[id3], telegram_message_id=10002)
    assert (await db.get_digest_item(id1))["telegram_message_id"] == 10001
    assert (await db.get_digest_item(id2))["telegram_message_id"] == 10001
    assert (await db.get_digest_item(id3))["telegram_message_id"] == 10002
