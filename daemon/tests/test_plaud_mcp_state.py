from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation_daemon.plaud_mcp_state import PlaudMcpStateDB
from automation_daemon.plaud_mcp_trigger import TrackState
from email_ingest.state import EmailIngestStateDB

UTC = timezone.utc
T0 = datetime(2026, 11, 7, 12, 0, 0, 123000, tzinfo=UTC)
MAP = {"Speaker 1": "Joel Low (Scope)", "Speaker 2": "Speaker 2"}


async def _db(tmp_path: Path) -> PlaudMcpStateDB:
    db = PlaudMcpStateDB(tmp_path / "state.db")
    await db.init_db()
    return db


async def test_upsert_and_get_roundtrip(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    track = TrackState(T0, MAP, None)
    await db.upsert_waiting("f_1", "Meeting", track, T0)
    row = await db.get("f_1")
    assert row is not None
    assert row.track == track
    assert row.status == "waiting_labels"
    assert row.attempts == 0
    assert row.name == "Meeting"
    assert row.first_seen_at == T0
    assert await db.get("missing") is None


async def test_upsert_keeps_attempts_and_status(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    await db.upsert_waiting("f_1", "Old", TrackState(T0, MAP, None), T0)
    assert await db.mark_ingesting("f_1") == 1
    await db.mark_retry("f_1", "boom")
    await db.upsert_waiting("f_1", "New", TrackState(T0, MAP, T0), T0)
    row = await db.get("f_1")
    assert row is not None
    assert row.attempts == 1
    assert row.status == "waiting_labels"
    assert row.name == "New"
    assert row.track.last_label_change_at == T0
    assert row.error == "boom"


async def test_mark_ingesting_is_durable(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    await db.upsert_waiting("f_1", "M", TrackState(T0, MAP, None), T0)
    await db.mark_ingesting("f_1")
    row = await PlaudMcpStateDB(tmp_path / "state.db").get("f_1")
    assert row is not None
    assert row.status == "ingesting"
    assert row.attempts == 1


async def test_track_state_survives_new_db_instance(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    changed = T0 + timedelta(minutes=5)
    track = TrackState(T0, MAP, changed)
    await db.upsert_waiting("f_1", "M", track, T0)
    row = await PlaudMcpStateDB(tmp_path / "state.db").get("f_1")
    assert row is not None
    assert row.track == track
    assert row.track.last_label_change_at == changed


async def test_recover_interrupted(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    for fid in ("f_1", "f_2"):
        await db.upsert_waiting(fid, fid, TrackState(T0, MAP, None), T0)
    await db.mark_ingesting("f_1")
    await db.mark_ingesting("f_2")
    await db.mark_completed("f_2", "/x.md", T0)
    assert await db.recover_interrupted() == ["f_1"]
    r1, r2 = await db.get("f_1"), await db.get("f_2")
    assert r1 is not None and r2 is not None
    assert (r1.status, r1.error) == ("failed", "interrupted")
    assert r2.status == "completed"


async def test_completed_ids_and_list_failed(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    for fid in ("f_1", "f_2", "f_3"):
        await db.upsert_waiting(fid, fid, TrackState(T0, MAP, None), T0)
    await db.mark_ingesting("f_1")
    await db.mark_completed("f_1", "/a.md", T0)
    await db.mark_ingesting("f_2")
    await db.mark_failed("f_2", "bad")
    assert await db.completed_ids() == {"f_1"}
    failed = await db.list_failed()
    assert [r.file_id for r in failed] == ["f_2"]
    assert failed[0].error == "bad"
    done = await db.get("f_1")
    assert done is not None
    assert done.transcript_path == "/a.md"
    assert done.ingested_at == T0


async def test_coexists_with_email_tables(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    await EmailIngestStateDB(path).init_db()
    await PlaudMcpStateDB(path).init_db()
