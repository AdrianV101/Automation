from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_daily_master_runs (
    target_date TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    master_path TEXT,
    item_count INTEGER,
    error TEXT
);
"""

NEWS_DAILY_MASTER_VALID_STATUSES = frozenset({
    "running",
    "completed",
    "skipped_empty",
    "failed",
    "failed_verification",
    "failed_notes_clobbered",
})

_TERMINAL_STATUSES = frozenset(NEWS_DAILY_MASTER_VALID_STATUSES) - {"running"}

_UPDATABLE_COLUMNS = frozenset({"master_path", "item_count", "error"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_key(d: date) -> str:
    return d.isoformat()


class NewsDailyMasterStateDB:
    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.executescript(_SCHEMA)
            await db.commit()

    async def insert_run(self, target_date: date) -> None:
        """Insert a fresh 'running' row for the date.

        If a row exists for this date (re-run scenario), it is replaced — the
        new run's start_at supersedes the old one.
        """
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO news_daily_master_runs "
                "(target_date, status, started_at, completed_at, master_path, item_count, error) "
                "VALUES (?, 'running', ?, NULL, NULL, NULL, NULL)",
                (_date_key(target_date), _now_iso()),
            )
            await db.commit()

    async def get_run(self, target_date: date) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM news_daily_master_runs WHERE target_date = ?",
                (_date_key(target_date),),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None
