from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

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

CREATE TABLE IF NOT EXISTS speaker_resolution_pending (
    message_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    unknown_speakers TEXT NOT NULL,
    name_map TEXT NOT NULL DEFAULT '{}',
    prompt_message_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
"""

VALID_STATUSES = frozenset({
    "received", "writing_pkm", "extracting", "completed", "failed", "dropped",
    "awaiting_speaker_labels", "timed_out_unresolved",
})

# Only these columns may be passed as kwargs to `update_status`. Guards against
# accidental (or malicious) SQL injection via dynamic column names.
_UPDATABLE_COLUMNS = frozenset({"transcript_path", "error", "summary_path"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_pending_row(d: dict[str, Any]) -> dict[str, Any] | None:
    """Decode the three JSON columns of a speaker_resolution_pending row dict.

    Returns the decoded dict on success, or None (and logs a warning) if any
    JSON column is malformed.  The caller should skip None rows rather than
    propagating the error.
    """
    try:
        d["unknown_speakers"] = json.loads(d["unknown_speakers"])
        d["name_map"] = json.loads(d["name_map"])
        d["prompt_message_ids"] = json.loads(d["prompt_message_ids"])
    except (json.JSONDecodeError, TypeError) as exc:
        log.warning(
            "skipping speaker_resolution_pending row %r — corrupt JSON: %s",
            d.get("message_id"),
            exc,
        )
        return None
    return d


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

    async def insert_pending(
        self, message_id: str, *, payload: str,
        unknown_speakers: list[str], created_at: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO speaker_resolution_pending "
                "(message_id, payload, unknown_speakers, name_map, "
                "prompt_message_ids, created_at) VALUES (?, ?, ?, '{}', '[]', ?)",
                (message_id, payload, json.dumps(unknown_speakers),
                 created_at or _now_iso()),
            )
            await db.commit()

    async def get_pending(self, message_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM speaker_resolution_pending WHERE message_id = ?",
                (message_id,),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        return _decode_pending_row(dict(row))

    async def set_pending_name(
        self, message_id: str, speaker_label: str, name: str,
    ) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT name_map FROM speaker_resolution_pending WHERE message_id = ?",
                (message_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                await db.rollback()
                raise KeyError(f"no pending row for {message_id!r}")
            name_map = json.loads(row[0])
            name_map[speaker_label] = name
            await db.execute(
                "UPDATE speaker_resolution_pending SET name_map = ? WHERE message_id = ?",
                (json.dumps(name_map), message_id),
            )
            await db.commit()

    async def set_pending_prompt_ids(
        self, message_id: str, message_ids: list[int],
    ) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "UPDATE speaker_resolution_pending SET prompt_message_ids = ? "
                "WHERE message_id = ?",
                (json.dumps(message_ids), message_id),
            )
            await db.commit()

    async def list_pending_older_than(
        self, cutoff_iso: str,
    ) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM speaker_resolution_pending "
                "WHERE created_at < ? ORDER BY created_at",
                (cutoff_iso,),
            ) as cur:
                rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            decoded = _decode_pending_row(dict(row))
            if decoded is not None:
                out.append(decoded)
        return out

    async def get_pending_by_prompt_id(
        self, prompt_message_id: int,
    ) -> dict[str, Any] | None:
        """Return the pending row whose prompt_message_ids contains
        `prompt_message_id`, or None.  Poison-resilient: a row with a
        corrupt JSON column is skipped, not fatal."""
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM speaker_resolution_pending",
            ) as cur:
                rows = await cur.fetchall()
        for row in rows:
            decoded = _decode_pending_row(dict(row))
            if decoded is None:
                continue
            if prompt_message_id in decoded["prompt_message_ids"]:
                return decoded
        return None

    async def delete_pending(self, message_id: str) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "DELETE FROM speaker_resolution_pending WHERE message_id = ?",
                (message_id,),
            )
            await db.commit()

    async def get_uidnext_checkpoint(self) -> int:
        raw = await self.get_setting("uidnext")
        return int(raw) if raw else 0

    async def set_uidnext_checkpoint(self, uidnext: int) -> None:
        await self.set_setting("uidnext", str(uidnext))

    async def record_processed(self, key: str, vault_path: str) -> None:
        """Protocol-conformant one-shot path; the transcript flow uses
        `insert_event` + `update_status` directly."""
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO email_ingest_events "
                "(message_id, uid, received_at, status) VALUES (?, 0, ?, 'received')",
                (key, _now_iso()),
            )
            cur = await db.execute(
                "UPDATE email_ingest_events "
                "SET status = 'completed', summary_path = ?, completed_at = ? "
                "WHERE message_id = ?",
                (vault_path, _now_iso(), key),
            )
            await db.commit()
            if cur.rowcount == 0:
                raise RuntimeError(
                    f"record_processed for {key!r} affected no rows",
                )

    # Generic-named aliases so a `NewsSourceState`-typed caller doesn't have
    # to know about IMAP-specific UIDNEXT semantics. The IMAP listener still
    # uses the *_uidnext_* methods directly — those are the canonical names.
    async def get_checkpoint(self) -> int:
        return await self.get_uidnext_checkpoint()

    async def set_checkpoint(self, checkpoint: int) -> None:
        await self.set_uidnext_checkpoint(checkpoint)

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
    SETTINGS_KEY_UIDNEXT = "uidnext:news"

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            # Both scripts run so a news-only deployment still has the shared settings table.
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

    async def record_processed(self, key: str, vault_path: str) -> None:
        """One-shot for pull-based sources (HN/FT/X) that have no intermediate
        "received but not yet written" window worth tracking. The IMAP-driven
        newsletter adapter still calls `insert_event` + `update_status` directly
        because IDLE push gives it a real intermediate state where a crash
        could otherwise leave a row stuck at 'received'."""
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO news_ingest_events "
                "(message_id, uid, received_at, status) VALUES (?, 0, ?, 'received')",
                (key, _now_iso()),
            )
            cur = await db.execute(
                "UPDATE news_ingest_events "
                "SET status = 'written', vault_note_path = ?, completed_at = ? "
                "WHERE message_id = ?",
                (vault_path, _now_iso(), key),
            )
            await db.commit()
            if cur.rowcount == 0:
                raise RuntimeError(
                    f"record_processed for {key!r} affected no rows",
                )

    async def get_checkpoint(self) -> int:
        return await self.get_uidnext_checkpoint()

    async def set_checkpoint(self, checkpoint: int) -> None:
        await self.set_uidnext_checkpoint(checkpoint)

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
