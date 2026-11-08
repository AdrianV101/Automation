"""SQLite state for Plaud MCP ingest: per-recording tracking and outcomes."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import aiosqlite

from .plaud_mcp_trigger import TrackState

PlaudStatus = Literal["waiting_labels", "ingesting", "completed", "failed"]


@dataclass(frozen=True)
class PlaudRow:
    file_id: str
    name: str
    status: PlaudStatus
    track: TrackState
    attempts: int
    error: str | None
    transcript_path: str | None
    first_seen_at: datetime
    ingested_at: datetime | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS plaud_mcp_recordings (
    file_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    ready_at TEXT NOT NULL,
    speaker_map TEXT NOT NULL,
    last_label_change_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    transcript_path TEXT,
    first_seen_at TEXT NOT NULL,
    ingested_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_plaud_mcp_recordings_status
    ON plaud_mcp_recordings(status);
"""


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _row(r: aiosqlite.Row) -> PlaudRow:
    changed = r["last_label_change_at"]
    ingested = r["ingested_at"]
    return PlaudRow(
        file_id=r["file_id"],
        name=r["name"],
        status=r["status"],
        track=TrackState(
            ready_at=_parse(r["ready_at"]),
            speaker_map=json.loads(r["speaker_map"]),
            last_label_change_at=_parse(changed) if changed else None,
        ),
        attempts=r["attempts"],
        error=r["error"],
        transcript_path=r["transcript_path"],
        first_seen_at=_parse(r["first_seen_at"]),
        ingested_at=_parse(ingested) if ingested else None,
    )


class PlaudMcpStateDB:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def init_db(self) -> None:
        async with aiosqlite.connect(str(self.path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.executescript(_SCHEMA)
            await db.commit()

    async def get(self, file_id: str) -> PlaudRow | None:
        async with aiosqlite.connect(str(self.path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM plaud_mcp_recordings WHERE file_id = ?", (file_id,),
            ) as cur:
                r = await cur.fetchone()
        return _row(r) if r else None

    async def upsert_waiting(
        self, file_id: str, name: str, track: TrackState, now: datetime,
    ) -> None:
        changed = track.last_label_change_at
        async with aiosqlite.connect(str(self.path)) as db:
            await db.execute(
                "INSERT INTO plaud_mcp_recordings "
                "(file_id, name, status, ready_at, speaker_map, "
                "last_label_change_at, attempts, first_seen_at) "
                "VALUES (?, ?, 'waiting_labels', ?, ?, ?, 0, ?) "
                "ON CONFLICT(file_id) DO UPDATE SET "
                "name = excluded.name, ready_at = excluded.ready_at, "
                "speaker_map = excluded.speaker_map, "
                "last_label_change_at = excluded.last_label_change_at",
                (
                    file_id, name, _iso(track.ready_at),
                    json.dumps(track.speaker_map, sort_keys=True),
                    _iso(changed) if changed else None, _iso(now),
                ),
            )
            await db.commit()

    async def mark_ingesting(self, file_id: str) -> int:
        async with aiosqlite.connect(str(self.path)) as db:
            await db.execute(
                "UPDATE plaud_mcp_recordings "
                "SET status = 'ingesting', attempts = attempts + 1 "
                "WHERE file_id = ?",
                (file_id,),
            )
            await db.commit()
            async with db.execute(
                "SELECT attempts FROM plaud_mcp_recordings WHERE file_id = ?",
                (file_id,),
            ) as cur:
                r = await cur.fetchone()
        return int(r[0]) if r else 0

    async def mark_completed(
        self, file_id: str, transcript_path: str | None, now: datetime,
    ) -> None:
        async with aiosqlite.connect(str(self.path)) as db:
            await db.execute(
                "UPDATE plaud_mcp_recordings SET status = 'completed', "
                "transcript_path = ?, ingested_at = ?, error = NULL "
                "WHERE file_id = ?",
                (transcript_path, _iso(now), file_id),
            )
            await db.commit()

    async def mark_retry(self, file_id: str, error: str) -> None:
        await self._set_status(file_id, "waiting_labels", error)

    async def mark_failed(self, file_id: str, error: str) -> None:
        await self._set_status(file_id, "failed", error)

    async def _set_status(self, file_id: str, status: str, error: str) -> None:
        async with aiosqlite.connect(str(self.path)) as db:
            await db.execute(
                "UPDATE plaud_mcp_recordings SET status = ?, error = ? "
                "WHERE file_id = ?",
                (status, error, file_id),
            )
            await db.commit()

    async def completed_ids(self) -> set[str]:
        async with aiosqlite.connect(str(self.path)) as db:
            async with db.execute(
                "SELECT file_id FROM plaud_mcp_recordings WHERE status = 'completed'",
            ) as cur:
                return {r[0] async for r in cur}

    async def list_failed(self) -> list[PlaudRow]:
        async with aiosqlite.connect(str(self.path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM plaud_mcp_recordings WHERE status = 'failed' "
                "ORDER BY first_seen_at, file_id",
            ) as cur:
                return [_row(r) async for r in cur]

    async def recover_interrupted(self) -> list[str]:
        async with aiosqlite.connect(str(self.path)) as db:
            async with db.execute(
                "SELECT file_id FROM plaud_mcp_recordings WHERE status = 'ingesting' "
                "ORDER BY file_id",
            ) as cur:
                ids = [r[0] async for r in cur]
            await db.execute(
                "UPDATE plaud_mcp_recordings SET status = 'failed', "
                "error = 'interrupted' WHERE status = 'ingesting'",
            )
            await db.commit()
        return ids
