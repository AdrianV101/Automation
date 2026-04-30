"""Conformance: both EmailIngestStateDB and NewsIngestStateDB satisfy NewsSourceState."""
from __future__ import annotations

import pytest

from news_pipeline import NewsSourceState

pytest.importorskip("email_ingest")  # only meaningful when sibling lib is installed
from email_ingest import EmailIngestStateDB, NewsIngestStateDB  # noqa: E402


def _is_news_source_state(obj: object) -> bool:
    """Structural check — Protocol classes don't expose runtime isinstance by default."""
    for name in (
        "get_uidnext_checkpoint",
        "set_uidnext_checkpoint",
        "is_processed",
        "record_processed",
    ):
        if not callable(getattr(obj, name, None)):
            return False
    return True


def test_news_ingest_state_db_satisfies_protocol(tmp_path):
    db = NewsIngestStateDB(tmp_path / "x.db")
    assert _is_news_source_state(db)
    # Type-only assertion; the structural check above does the work at runtime.
    _: NewsSourceState = db


def test_email_ingest_state_db_satisfies_protocol(tmp_path):
    db = EmailIngestStateDB(tmp_path / "x.db")
    assert _is_news_source_state(db)
    _: NewsSourceState = db


async def test_news_record_processed_marks_written(tmp_path):
    db = NewsIngestStateDB(tmp_path / "n.db")
    await db.init_db()
    await db.record_processed("msg1", "00-Inbox/news/2026-04-30/foo.md")
    assert await db.is_processed("msg1")
    event = await db.get_event("msg1")
    assert event is not None
    assert event["status"] == "written"
    assert event["vault_note_path"] == "00-Inbox/news/2026-04-30/foo.md"
    assert event["completed_at"]


async def test_news_record_processed_idempotent(tmp_path):
    db = NewsIngestStateDB(tmp_path / "n.db")
    await db.init_db()
    await db.record_processed("msg1", "path/a.md")
    await db.record_processed("msg1", "path/b.md")  # second call updates path
    event = await db.get_event("msg1")
    assert event["vault_note_path"] == "path/b.md"


async def test_email_record_processed_marks_completed(tmp_path):
    db = EmailIngestStateDB(tmp_path / "e.db")
    await db.init_db()
    await db.record_processed("msg1", "00-Inbox/transcripts/2026-04-30/foo.md")
    assert await db.is_processed("msg1")
    event = await db.get_event("msg1")
    assert event is not None
    assert event["status"] == "completed"
    assert event["summary_path"] == "00-Inbox/transcripts/2026-04-30/foo.md"


async def test_news_checkpoint_round_trip(tmp_path):
    db = NewsIngestStateDB(tmp_path / "n.db")
    await db.init_db()
    assert await db.get_uidnext_checkpoint() == 0
    await db.set_uidnext_checkpoint(42)
    assert await db.get_uidnext_checkpoint() == 42
