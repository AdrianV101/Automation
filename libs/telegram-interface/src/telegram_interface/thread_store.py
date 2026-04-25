"""SQLite-backed thread + session persistence for Telegram topics.

ThreadStore owns the threads table and directly implements the
agent_infra SessionStore protocol, eliminating the need for a
separate adapter class.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from agent_infra import SessionStore  # noqa: TCH002 – runtime isinstance

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ThreadRecord:
    thread_id: int
    session_id: str
    name: str
    command: str
    created_at: str


class ThreadStore:
    """SQLite-backed thread + session persistence. Implements SessionStore."""

    def __init__(self, db_path: Path):
        self._db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS threads (
                    thread_id   INTEGER PRIMARY KEY,
                    session_id  TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    command     TEXT NOT NULL,
                    created_at  TEXT NOT NULL
                )
            """)
            await db.commit()

    # --- Helpers ---

    @staticmethod
    def _parse_thread_id(key: str) -> int | None:
        """Extract integer thread_id from an opaque key.

        Accepts "42" or "thread-42" (prefix-integer) formats.
        Returns None if the key cannot be parsed.
        """
        # Try plain integer first
        try:
            return int(key)
        except (ValueError, TypeError):
            pass
        # Try "prefix-integer" format (e.g. "thread-42")
        try:
            return int(key.split("-", 1)[1])
        except (IndexError, ValueError, TypeError, AttributeError):
            return None

    # --- SessionStore protocol ---

    async def get_session_id(self, key: str) -> str | None:
        """Look up session_id by opaque key.

        Accepts both plain integers ("42") and prefixed keys ("thread-42").
        """
        thread_id = self._parse_thread_id(key)
        if thread_id is None:
            return None
        record = await self.get_thread(thread_id)
        return record.session_id if record else None

    async def save_session_id(self, key: str, session_id: str) -> None:
        """Save session_id for an opaque key. Upserts.

        Accepts both plain integers ("42") and prefixed keys ("thread-42").
        """
        thread_id = self._parse_thread_id(key)
        if thread_id is None:
            log.warning("Cannot save session_id for non-integer key %r", key)
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO threads (thread_id, session_id, name, command, created_at) "
                "VALUES (?, ?, '', '', ?) "
                "ON CONFLICT(thread_id) DO UPDATE SET session_id = excluded.session_id",
                (thread_id, session_id, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

    # --- Telegram-specific methods ---

    async def save_thread(
        self, thread_id: int, session_id: str, name: str, command: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO threads (thread_id, session_id, name, command, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(thread_id) DO UPDATE SET session_id = excluded.session_id",
                (thread_id, session_id, name, command, now),
            )
            await db.commit()

    async def get_thread(self, thread_id: int) -> ThreadRecord | None:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT thread_id, session_id, name, command, created_at "
                "FROM threads WHERE thread_id = ?",
                (thread_id,),
            )
            row = await cursor.fetchone()
            return ThreadRecord(*row) if row else None

    async def update_session_id(self, thread_id: int, session_id: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE threads SET session_id = ? WHERE thread_id = ?",
                (session_id, thread_id),
            )
            await db.commit()

    async def list_threads(self) -> list[ThreadRecord]:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT thread_id, session_id, name, command, created_at "
                "FROM threads ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
            return [ThreadRecord(*row) for row in rows]
