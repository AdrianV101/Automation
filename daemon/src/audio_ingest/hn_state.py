"""HN-specific state DB. Reuses the shared SQLite file (email_ingest_state.db).
Satisfies news_pipeline.NewsSourceState so the listener-facing surface is
uniform across sources, while keeping the per-source dedupe-key shape (HN
item_id is an int, not a Message-ID string)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

DEDUPE_KEY_PREFIX = "hn-"
SETTINGS_KEY_CHECKPOINT = "hn:largest_seen_item_id"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hn_ingest_events (
    item_id INTEGER PRIMARY KEY,
    points INTEGER NOT NULL DEFAULT 0,
    title TEXT,
    vault_note_path TEXT,
    ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS email_ingest_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_item_id(key: str) -> int:
    if not key.startswith(DEDUPE_KEY_PREFIX):
        raise ValueError(
            f"HN dedupe key must start with {DEDUPE_KEY_PREFIX!r}, got {key!r}",
        )
    try:
        return int(key[len(DEDUPE_KEY_PREFIX):])
    except ValueError as exc:
        raise ValueError(f"HN dedupe key has non-integer suffix: {key!r}") from exc


class HackerNewsStateDB:
    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.executescript(_SCHEMA)
            await db.commit()

    async def is_processed(self, key: str) -> bool:
        item_id = _parse_item_id(key)
        async with aiosqlite.connect(self._path) as db:
            async with db.execute(
                "SELECT 1 FROM hn_ingest_events WHERE item_id = ?", (item_id,),
            ) as cur:
                return (await cur.fetchone()) is not None

    async def record_processed(self, key: str, vault_path: str) -> None:
        item_id = _parse_item_id(key)
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO hn_ingest_events "
                "(item_id, points, title, vault_note_path, ingested_at) "
                "VALUES (?, 0, NULL, NULL, ?)",
                (item_id, _now_iso()),
            )
            cur = await db.execute(
                "UPDATE hn_ingest_events "
                "SET vault_note_path = ?, ingested_at = ? "
                "WHERE item_id = ?",
                (vault_path, _now_iso(), item_id),
            )
            await db.commit()
            if cur.rowcount == 0:
                raise RuntimeError(
                    f"record_processed for {key!r} affected no rows",
                )

    async def record_processed_full(
        self, key: str, *, points: int, title: str | None, vault_path: str,
    ) -> None:
        """Like record_processed but stores points and title on the dedupe row.

        Same key shape as record_processed (`hn-{item_id}`) — adapter callers
        already have the key in scope.
        """
        item_id = _parse_item_id(key)
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO hn_ingest_events "
                "(item_id, points, title, vault_note_path, ingested_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (item_id, points, title, vault_path, _now_iso()),
            )
            cur = await db.execute(
                "UPDATE hn_ingest_events "
                "SET points = ?, title = ?, vault_note_path = ?, ingested_at = ? "
                "WHERE item_id = ?",
                (points, title, vault_path, _now_iso(), item_id),
            )
            await db.commit()
            if cur.rowcount == 0:
                raise RuntimeError(
                    f"record_processed_full for item_id={item_id} affected no rows",
                )

    async def get_event(self, item_id: int) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM hn_ingest_events WHERE item_id = ?", (item_id,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_checkpoint(self) -> int:
        raw = await self._get_setting(SETTINGS_KEY_CHECKPOINT)
        return int(raw) if raw else 0

    async def set_checkpoint(self, checkpoint: int) -> None:
        await self._set_setting(SETTINGS_KEY_CHECKPOINT, str(checkpoint))

    async def _get_setting(self, key: str) -> str | None:
        async with aiosqlite.connect(self._path) as db:
            async with db.execute(
                "SELECT value FROM email_ingest_settings WHERE key = ?", (key,),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    async def _set_setting(self, key: str, value: str) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT INTO email_ingest_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            await db.commit()
