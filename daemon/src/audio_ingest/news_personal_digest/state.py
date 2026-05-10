from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_digest_runs (
    digest_date TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    item_count INTEGER,
    rating_signal_summary TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS news_digest_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_date TEXT NOT NULL,
    source_path TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    position INTEGER NOT NULL,
    telegram_message_id INTEGER,
    FOREIGN KEY (digest_date) REFERENCES news_digest_runs(digest_date)
);

CREATE INDEX IF NOT EXISTS idx_news_digest_items_date
  ON news_digest_items(digest_date);

CREATE TABLE IF NOT EXISTS news_ratings (
    item_id INTEGER PRIMARY KEY,
    digest_date TEXT NOT NULL,
    rating TEXT NOT NULL,
    rated_at TEXT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES news_digest_items(id)
);

CREATE INDEX IF NOT EXISTS idx_news_ratings_date
  ON news_ratings(digest_date);
"""

NEWS_DIGEST_VALID_STATUSES = frozenset({
    "running",
    "completed",
    "skipped_no_master",
    "failed",
    "failed_verification",
})

VALID_RATINGS = frozenset({"thumbs_up", "thumbs_down", "star"})

_TERMINAL_STATUSES = frozenset(NEWS_DIGEST_VALID_STATUSES) - {"running"}

_UPDATABLE_COLUMNS = frozenset({
    "item_count", "rating_signal_summary", "error",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_key(d: date) -> str:
    return d.isoformat()


class NewsDigestStateDB:
    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.executescript(_SCHEMA)
            await db.commit()

    async def insert_run(self, digest_date: date) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO news_digest_runs "
                "(digest_date, status, started_at, completed_at, item_count, "
                " rating_signal_summary, error) "
                "VALUES (?, 'running', ?, NULL, NULL, NULL, NULL)",
                (_date_key(digest_date), _now_iso()),
            )
            await db.commit()

    async def get_run(self, digest_date: date) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM news_digest_runs WHERE digest_date = ?",
                (_date_key(digest_date),),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None
