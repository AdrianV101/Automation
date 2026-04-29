from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from email_ingest.state import (
    EmailIngestStateDB, EmailIngestStatusTracker, NewsIngestStateDB,
)


@pytest.fixture
async def db(tmp_path: Path) -> EmailIngestStateDB:
    d = EmailIngestStateDB(tmp_path / "email.db")
    await d.init_db()
    return d


async def test_insert_and_fetch_event(db: EmailIngestStateDB) -> None:
    await db.insert_event(message_id="<abc@x>", uid=42, subject="hi")
    row = await db.get_event("<abc@x>")
    assert row is not None
    assert row["uid"] == 42
    assert row["subject"] == "hi"
    assert row["status"] == "received"


async def test_dedupe_on_message_id(db: EmailIngestStateDB) -> None:
    await db.insert_event(message_id="<abc@x>", uid=42, subject="hi")
    already = await db.is_processed("<abc@x>")
    assert already is True
    missing = await db.is_processed("<nope@x>")
    assert missing is False


async def test_update_status_transitions(db: EmailIngestStateDB) -> None:
    await db.insert_event(message_id="<abc@x>", uid=42, subject="hi")
    await db.update_status("<abc@x>", "writing_pkm", transcript_path="/vault/t.md")
    row = await db.get_event("<abc@x>")
    assert row["status"] == "writing_pkm"
    assert row["transcript_path"] == "/vault/t.md"


async def test_uidnext_checkpoint(db: EmailIngestStateDB) -> None:
    assert await db.get_uidnext_checkpoint() == 0
    await db.set_uidnext_checkpoint(1234)
    assert await db.get_uidnext_checkpoint() == 1234


async def test_status_tracker_updates_row(db: EmailIngestStateDB) -> None:
    await db.insert_event(message_id="<abc@x>", uid=42, subject="hi")
    tracker = EmailIngestStatusTracker(db, "<abc@x>")
    await tracker.update("extracting")
    row = await db.get_event("<abc@x>")
    assert row["status"] == "extracting"


async def test_mark_failed_writes_error(db: EmailIngestStateDB) -> None:
    await db.insert_event(message_id="<abc@x>", uid=42, subject="hi")
    await db.update_status("<abc@x>", "failed", error="boom")
    row = await db.get_event("<abc@x>")
    assert row["status"] == "failed"
    assert row["error"] == "boom"


async def test_generic_settings_kv(db: EmailIngestStateDB) -> None:
    assert await db.get_setting("pipeline_thread_id") is None
    await db.set_setting("pipeline_thread_id", "42")
    assert await db.get_setting("pipeline_thread_id") == "42"
    await db.set_setting("pipeline_thread_id", "43")  # upsert
    assert await db.get_setting("pipeline_thread_id") == "43"


async def test_update_status_accepts_summary_path(db: EmailIngestStateDB) -> None:
    # Regression: pipeline.py passes summary_path on the completed path.
    # If the column were missing from the schema, SQLite would raise
    # OperationalError and every successful email would look failed.
    await db.insert_event(message_id="<abc@x>", uid=1, subject="hi")
    await db.update_status(
        "<abc@x>", "completed",
        transcript_path="/vault/t.md", summary_path="/vault/s.md",
    )
    row = await db.get_event("<abc@x>")
    assert row["summary_path"] == "/vault/s.md"


async def test_update_status_rejects_unknown_column(db: EmailIngestStateDB) -> None:
    await db.insert_event(message_id="<abc@x>", uid=1)
    with pytest.raises(ValueError):
        await db.update_status("<abc@x>", "completed", nonsense_column="x")


async def test_update_status_raises_keyerror_on_missing_row(db: EmailIngestStateDB) -> None:
    # The orchestrator's insert-event-first invariant relies on this contract:
    # if a caller updates a row that was never inserted, the bug surfaces loudly.
    with pytest.raises(KeyError):
        await db.update_status("<never-inserted@x>", "failed", error="boom")


# --- NewsIngestStateDB ----------------------------------------------------


async def test_news_state_db_init_creates_table(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    db = NewsIngestStateDB(path)
    await db.init_db()
    async with aiosqlite.connect(str(path)) as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='news_ingest_events'",
        ) as cur:
            row = await cur.fetchone()
    assert row is not None


async def test_news_state_db_insert_and_dedupe(tmp_path: Path) -> None:
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    await db.insert_event("msg1", uid=10, sender="a@x.com", subject="hi")
    assert await db.is_processed("msg1") is True
    assert await db.is_processed("msg2") is False
    # second insert with same id is a no-op (INSERT OR IGNORE)
    await db.insert_event("msg1", uid=99, sender="other@x.com", subject="other")
    event = await db.get_event("msg1")
    assert event is not None
    assert event["uid"] == 10
    assert event["sender"] == "a@x.com"
    assert event["status"] == "received"


async def test_news_state_db_update_status_writes_completed_at(tmp_path: Path) -> None:
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    await db.insert_event("msg1", uid=1)
    await db.update_status(
        "msg1", "written", vault_note_path="00-Inbox/news/2026-04-29/foo.md",
    )
    event = await db.get_event("msg1")
    assert event is not None
    assert event["status"] == "written"
    assert event["vault_note_path"] == "00-Inbox/news/2026-04-29/foo.md"
    assert event["completed_at"] is not None  # 'written' is terminal


async def test_news_state_db_rejects_invalid_status(tmp_path: Path) -> None:
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    await db.insert_event("msg1", uid=1)
    with pytest.raises(ValueError, match="invalid status"):
        await db.update_status("msg1", "bogus")


async def test_news_state_db_rejects_unknown_column(tmp_path: Path) -> None:
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    await db.insert_event("msg1", uid=1)
    with pytest.raises(ValueError, match="unknown columns"):
        # transcript_path belongs on EmailIngestStateDB, not on news.
        await db.update_status("msg1", "written", transcript_path="x")


async def test_news_state_db_update_status_raises_keyerror_on_missing(tmp_path: Path) -> None:
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    with pytest.raises(KeyError):
        await db.update_status("never-inserted", "failed", error="boom")


async def test_news_state_db_uidnext_round_trip(tmp_path: Path) -> None:
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    assert await db.get_uidnext_checkpoint() == 0  # default
    await db.set_uidnext_checkpoint(123)
    assert await db.get_uidnext_checkpoint() == 123


async def test_news_uidnext_isolated_from_email_uidnext(tmp_path: Path) -> None:
    """Same DB file, two state classes, different settings keys must not clash."""
    path = tmp_path / "state.db"
    email_db = EmailIngestStateDB(path)
    news_db = NewsIngestStateDB(path)
    await email_db.init_db()
    await news_db.init_db()
    await email_db.set_uidnext_checkpoint(50)
    await news_db.set_uidnext_checkpoint(900)
    assert await email_db.get_uidnext_checkpoint() == 50
    assert await news_db.get_uidnext_checkpoint() == 900
