from __future__ import annotations

import pytest

from email_ingest.state import EmailIngestStateDB


@pytest.mark.asyncio
async def test_awaiting_and_timed_out_are_valid_statuses(tmp_path) -> None:
    db = EmailIngestStateDB(tmp_path / "s.db")
    await db.init_db()
    await db.insert_event("m1", uid=1, subject="s")
    await db.update_status("m1", "awaiting_speaker_labels")
    assert (await db.get_event("m1"))["status"] == "awaiting_speaker_labels"
    await db.update_status("m1", "timed_out_unresolved")
    assert (await db.get_event("m1"))["status"] == "timed_out_unresolved"


@pytest.mark.asyncio
async def test_pending_insert_get_roundtrip(tmp_path) -> None:
    db = EmailIngestStateDB(tmp_path / "s.db")
    await db.init_db()
    await db.insert_pending("m1", payload="JOB_BLOB", unknown_speakers=["Speaker 1", "Speaker 2"])
    row = await db.get_pending("m1")
    assert row["payload"] == "JOB_BLOB"
    assert row["unknown_speakers"] == ["Speaker 1", "Speaker 2"]
    assert row["name_map"] == {}
    assert row["prompt_message_ids"] == []


@pytest.mark.asyncio
async def test_set_name_and_prompt_ids_accumulate(tmp_path) -> None:
    db = EmailIngestStateDB(tmp_path / "s.db")
    await db.init_db()
    await db.insert_pending("m1", payload="B", unknown_speakers=["Speaker 1", "Speaker 2"])
    await db.set_pending_prompt_ids("m1", [101, 102])
    await db.set_pending_name("m1", "Speaker 1", "Alice")
    row = await db.get_pending("m1")
    assert row["name_map"] == {"Speaker 1": "Alice"}
    assert row["prompt_message_ids"] == [101, 102]


@pytest.mark.asyncio
async def test_list_pending_older_than(tmp_path) -> None:
    db = EmailIngestStateDB(tmp_path / "s.db")
    await db.init_db()
    await db.insert_pending("old", payload="B", unknown_speakers=["Speaker 1"],
                            created_at="2000-01-01T00:00:00+00:00")
    await db.insert_pending("new", payload="B", unknown_speakers=["Speaker 1"],
                            created_at="2999-01-01T00:00:00+00:00")
    stale = await db.list_pending_older_than("2026-05-15T00:00:00+00:00")
    assert [r["message_id"] for r in stale] == ["old"]


@pytest.mark.asyncio
async def test_delete_pending(tmp_path) -> None:
    db = EmailIngestStateDB(tmp_path / "s.db")
    await db.init_db()
    await db.insert_pending("m1", payload="B", unknown_speakers=["Speaker 1"])
    await db.delete_pending("m1")
    assert await db.get_pending("m1") is None


@pytest.mark.asyncio
async def test_set_pending_name_concurrent_no_lost_update(tmp_path) -> None:
    import asyncio
    db = EmailIngestStateDB(tmp_path / "s.db"); await db.init_db()
    await db.insert_pending("m1", payload="B",
                            unknown_speakers=["Speaker 1", "Speaker 2"])
    await asyncio.gather(
        db.set_pending_name("m1", "Speaker 1", "Alice"),
        db.set_pending_name("m1", "Speaker 2", "Bob"),
    )
    row = await db.get_pending("m1")
    assert row["name_map"] == {"Speaker 1": "Alice", "Speaker 2": "Bob"}  # neither lost


@pytest.mark.asyncio
async def test_list_pending_skips_poisoned_row(tmp_path) -> None:
    db = EmailIngestStateDB(tmp_path / "s.db"); await db.init_db()
    await db.insert_pending("good", payload="B", unknown_speakers=["Speaker 1"],
                            created_at="2000-01-01T00:00:00+00:00")
    # Corrupt one row's JSON column directly.
    import aiosqlite
    async with aiosqlite.connect(tmp_path / "s.db") as raw:
        await raw.execute(
            "INSERT INTO speaker_resolution_pending "
            "(message_id, payload, unknown_speakers, name_map, prompt_message_ids, created_at) "
            "VALUES ('bad','B','{not json','{}','[]','2000-01-01T00:00:00+00:00')")
        await raw.commit()
    rows = await db.list_pending_older_than("2026-01-01T00:00:00+00:00")
    ids = [r["message_id"] for r in rows]
    assert "good" in ids and "bad" not in ids  # poisoned row skipped, not fatal


@pytest.mark.asyncio
async def test_get_pending_by_prompt_id(tmp_path) -> None:
    db = EmailIngestStateDB(tmp_path / "s.db"); await db.init_db()
    await db.insert_pending("m1", payload="B",
                            unknown_speakers=["Speaker 1", "Speaker 2"])
    await db.set_pending_prompt_ids("m1", [101, 102])
    await db.insert_pending("m2", payload="B", unknown_speakers=["Speaker 1"])
    await db.set_pending_prompt_ids("m2", [201])
    hit = await db.get_pending_by_prompt_id(102)
    assert hit is not None and hit["message_id"] == "m1"
    assert hit["prompt_message_ids"] == [101, 102]
    assert await db.get_pending_by_prompt_id(999) is None
