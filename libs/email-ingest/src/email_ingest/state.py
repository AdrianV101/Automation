from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS email_ingest_events (
    message_id TEXT PRIMARY KEY,
    uid INTEGER NOT NULL,
    received_at TEXT NOT NULL,
    subject TEXT,
    transcript_path TEXT,
    summary_path TEXT,
    status TEXT NOT NULL DEFAULT 'received',
    error TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS email_ingest_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

VALID_STATUSES = frozenset({
    "received", "writing_pkm", "extracting", "completed", "failed", "dropped",
})

# Only these columns may be passed as kwargs to `update_status`. Guards against
# accidental (or malicious) SQL injection via dynamic column names.
_UPDATABLE_COLUMNS = frozenset({"transcript_path", "error", "summary_path"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EmailIngestStateDB:
    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._path) as db:
            # WAL lets readers and a single writer proceed concurrently — this
            # DB is shared with ThreadStore, so default rollback journal mode
            # would serialise every cross-table operation and produce
            # "database is locked" errors under normal load.
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.executescript(_SCHEMA)
            await db.commit()

    async def insert_event(
        self, message_id: str, uid: int, subject: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO email_ingest_events "
                "(message_id, uid, received_at, subject, status) "
                "VALUES (?, ?, ?, ?, 'received')",
                (message_id, uid, _now_iso(), subject),
            )
            await db.commit()

    async def is_processed(self, message_id: str) -> bool:
        async with aiosqlite.connect(self._path) as db:
            async with db.execute(
                "SELECT 1 FROM email_ingest_events WHERE message_id = ?",
                (message_id,),
            ) as cur:
                return (await cur.fetchone()) is not None

    async def get_event(self, message_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM email_ingest_events WHERE message_id = ?",
                (message_id,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def update_status(
        self, message_id: str, status: str, **fields: str | None,
    ) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status {status!r}")
        bad = set(fields) - _UPDATABLE_COLUMNS
        if bad:
            raise ValueError(f"unknown columns: {sorted(bad)}")
        cols = ["status = ?"]
        params: list[Any] = [status]
        for key, value in fields.items():
            cols.append(f"{key} = ?")
            params.append(value)
        if status in ("completed", "failed", "dropped"):
            cols.append("completed_at = ?")
            params.append(_now_iso())
        params.append(message_id)
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute(
                f"UPDATE email_ingest_events SET {', '.join(cols)} WHERE message_id = ?",
                params,
            )
            await db.commit()
            if cur.rowcount == 0:
                raise KeyError(f"no event row for message_id={message_id!r}")

    async def get_uidnext_checkpoint(self) -> int:
        raw = await self.get_setting("uidnext")
        return int(raw) if raw else 0

    async def set_uidnext_checkpoint(self, uidnext: int) -> None:
        await self.set_setting("uidnext", str(uidnext))

    async def get_setting(self, key: str) -> str | None:
        async with aiosqlite.connect(self._path) as db:
            async with db.execute(
                "SELECT value FROM email_ingest_settings WHERE key = ?", (key,),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT INTO email_ingest_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            await db.commit()


class EmailIngestStatusTracker:
    def __init__(self, db: EmailIngestStateDB, message_id: str) -> None:
        self._db = db
        self._id = message_id

    async def update(self, status: str, **kwargs: str | None) -> None:
        await self._db.update_status(self._id, status, **kwargs)


_NEWS_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_ingest_events (
    message_id TEXT PRIMARY KEY,
    uid INTEGER NOT NULL,
    received_at TEXT NOT NULL,
    sender TEXT,
    subject TEXT,
    vault_note_path TEXT,
    status TEXT NOT NULL DEFAULT 'received',
    error TEXT,
    completed_at TEXT
);
"""

NEWS_VALID_STATUSES = frozenset({"received", "written", "failed", "dropped"})
_NEWS_UPDATABLE_COLUMNS = frozenset({"vault_note_path", "error"})
_NEWS_TERMINAL_STATUSES = frozenset({"written", "failed", "dropped"})


class NewsIngestStateDB:
    """State store for newsletter ingestion. Coexists with EmailIngestStateDB
    in the same SQLite file by using a parallel `news_ingest_events` table
    and a namespaced settings key for UIDNEXT (so two listeners on the same
    file don't clobber each other's checkpoint)."""

    SETTINGS_KEY_UIDNEXT = "uidnext:news"

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            # Both schemas coexist in this file. Run both idempotently so a
            # news-only deployment still gets the shared settings table.
            await db.executescript(_SCHEMA)
            await db.executescript(_NEWS_SCHEMA)
            await db.commit()

    async def insert_event(
        self, message_id: str, uid: int,
        sender: str | None = None, subject: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO news_ingest_events "
                "(message_id, uid, received_at, sender, subject, status) "
                "VALUES (?, ?, ?, ?, ?, 'received')",
                (message_id, uid, _now_iso(), sender, subject),
            )
            await db.commit()

    async def is_processed(self, message_id: str) -> bool:
        async with aiosqlite.connect(self._path) as db:
            async with db.execute(
                "SELECT 1 FROM news_ingest_events WHERE message_id = ?",
                (message_id,),
            ) as cur:
                return (await cur.fetchone()) is not None

    async def get_event(self, message_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM news_ingest_events WHERE message_id = ?",
                (message_id,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def update_status(
        self, message_id: str, status: str, **fields: str | None,
    ) -> None:
        if status not in NEWS_VALID_STATUSES:
            raise ValueError(f"invalid status {status!r}")
        bad = set(fields) - _NEWS_UPDATABLE_COLUMNS
        if bad:
            raise ValueError(f"unknown columns: {sorted(bad)}")
        cols = ["status = ?"]
        params: list[Any] = [status]
        for key, value in fields.items():
            cols.append(f"{key} = ?")
            params.append(value)
        if status in _NEWS_TERMINAL_STATUSES:
            cols.append("completed_at = ?")
            params.append(_now_iso())
        params.append(message_id)
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute(
                f"UPDATE news_ingest_events SET {', '.join(cols)} WHERE message_id = ?",
                params,
            )
            await db.commit()
            if cur.rowcount == 0:
                raise KeyError(f"no event row for message_id={message_id!r}")

    async def get_uidnext_checkpoint(self) -> int:
        raw = await self._get_setting(self.SETTINGS_KEY_UIDNEXT)
        return int(raw) if raw else 0

    async def set_uidnext_checkpoint(self, uidnext: int) -> None:
        await self._set_setting(self.SETTINGS_KEY_UIDNEXT, str(uidnext))

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
