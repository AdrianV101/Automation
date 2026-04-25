from __future__ import annotations

from pathlib import Path

import pytest

from email_ingest.state import EmailIngestStateDB, EmailIngestStatusTracker


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
