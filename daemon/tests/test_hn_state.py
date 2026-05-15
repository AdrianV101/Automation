from __future__ import annotations

import pytest

from news_pipeline import NewsSourceState


@pytest.fixture
async def db(tmp_path):
    from automation_daemon.hn_state import HackerNewsStateDB
    state = HackerNewsStateDB(tmp_path / "state.db")
    await state.init_db()
    return state


def test_satisfies_news_source_state_protocol(tmp_path):
    from automation_daemon.hn_state import HackerNewsStateDB
    state = HackerNewsStateDB(tmp_path / "x.db")
    assert isinstance(state, NewsSourceState)


async def test_checkpoint_starts_at_zero_then_round_trips(db):
    assert await db.get_checkpoint() == 0
    await db.set_checkpoint(99999999)
    assert await db.get_checkpoint() == 99999999


async def test_is_processed_false_for_unknown_key(db):
    assert await db.is_processed("hn-1") is False


async def test_record_processed_marks_processed(db):
    await db.record_processed("hn-1", "00-Inbox/news/2026-05-01/foo-abc123.md")
    assert await db.is_processed("hn-1") is True


async def test_record_processed_idempotent(db):
    await db.record_processed("hn-1", "path/a.md")
    await db.record_processed("hn-1", "path/b.md")
    assert await db.is_processed("hn-1") is True
    row = await db.get_event(1)
    assert row["vault_note_path"] == "path/b.md"


async def test_record_processed_persists_vault_path(db):
    await db.record_processed("hn-42", "00-Inbox/news/2026-05-01/x-abc123.md")
    row = await db.get_event(42)
    assert row is not None
    assert row["vault_note_path"] == "00-Inbox/news/2026-05-01/x-abc123.md"
    assert row["item_id"] == 42


async def test_record_processed_full_persists_metadata(db):
    await db.record_processed_full(
        "hn-99", points=234, title="Some HN headline",
        vault_path="00-Inbox/news/2026-05-01/some-headline-abcdef.md",
    )
    assert await db.is_processed("hn-99") is True
    row = await db.get_event(99)
    assert row is not None
    assert row["item_id"] == 99
    assert row["points"] == 234
    assert row["title"] == "Some HN headline"
    assert row["vault_note_path"] == "00-Inbox/news/2026-05-01/some-headline-abcdef.md"


async def test_record_processed_full_idempotent_overwrites_metadata(db):
    await db.record_processed_full(
        "hn-7", points=100, title="first", vault_path="path/a.md",
    )
    await db.record_processed_full(
        "hn-7", points=250, title="updated", vault_path="path/b.md",
    )
    row = await db.get_event(7)
    assert row["points"] == 250
    assert row["title"] == "updated"
    assert row["vault_note_path"] == "path/b.md"


async def test_record_processed_full_rejects_non_hn_prefix_key(db):
    with pytest.raises(ValueError, match="hn-"):
        await db.record_processed_full(
            "not-an-hn-key", points=1, title=None, vault_path="vault/path.md",
        )


async def test_record_processed_rejects_non_hn_prefix_key(db):
    """The contract is that callers always use the f'hn-{item_id}' shape."""
    with pytest.raises(ValueError, match="hn-"):
        await db.record_processed("not-an-hn-key", "vault/path.md")


async def test_record_processed_raises_when_update_affects_zero_rows(tmp_path):
    """If the table is dropped between INSERT-OR-IGNORE and UPDATE, raise."""
    import aiosqlite
    from automation_daemon.hn_state import HackerNewsStateDB
    db = HackerNewsStateDB(tmp_path / "n.db")
    await db.init_db()
    async with aiosqlite.connect(str(tmp_path / "n.db")) as conn:
        await conn.execute("DROP TABLE hn_ingest_events")
        await conn.commit()
    with pytest.raises(Exception):
        await db.record_processed("hn-1", "vault/path.md")


async def test_coexists_with_email_ingest_state_db(tmp_path):
    """Both classes must be able to init_db on the same SQLite file."""
    from email_ingest import EmailIngestStateDB
    from automation_daemon.hn_state import HackerNewsStateDB
    path = tmp_path / "shared.db"
    email_db = EmailIngestStateDB(path)
    hn_db = HackerNewsStateDB(path)
    await email_db.init_db()
    await hn_db.init_db()
    # And the reverse order also works.
    path2 = tmp_path / "shared2.db"
    hn_db2 = HackerNewsStateDB(path2)
    email_db2 = EmailIngestStateDB(path2)
    await hn_db2.init_db()
    await email_db2.init_db()
