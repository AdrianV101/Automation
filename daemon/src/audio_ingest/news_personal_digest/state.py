from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite


@dataclass(frozen=True)
class DigestItemInput:
    source_path: str
    category: str
    title: str
    url: str | None
    position: int

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

    async def update_run(
        self, digest_date: date, status: str, **fields: Any,
    ) -> None:
        if status not in NEWS_DIGEST_VALID_STATUSES:
            raise ValueError(f"invalid status {status!r}")
        bad = set(fields) - _UPDATABLE_COLUMNS
        if bad:
            raise ValueError(f"unknown columns: {sorted(bad)}")
        cols = ["status = ?"]
        params: list[Any] = [status]
        for key, value in fields.items():
            cols.append(f"{key} = ?")
            params.append(value)
        if status in _TERMINAL_STATUSES:
            cols.append("completed_at = ?")
            params.append(_now_iso())
        params.append(_date_key(digest_date))
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute(
                f"UPDATE news_digest_runs SET {', '.join(cols)} "
                f"WHERE digest_date = ?",
                params,
            )
            await db.commit()
            if cur.rowcount == 0:
                raise KeyError(
                    f"no run row for digest_date={digest_date.isoformat()!r}",
                )

    async def insert_item(
        self, *, digest_date: date, item: DigestItemInput,
    ) -> int:
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute(
                "INSERT INTO news_digest_items "
                "(digest_date, source_path, category, title, url, position, "
                " telegram_message_id) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (
                    _date_key(digest_date),
                    item.source_path, item.category, item.title,
                    item.url, item.position,
                ),
            )
            await db.commit()
            row_id = cur.lastrowid
            if row_id is None:
                raise RuntimeError("INSERT returned no lastrowid")
            return int(row_id)

    async def get_digest_item(self, item_id: int) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM news_digest_items WHERE id = ?", (item_id,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def attach_telegram_message_id(
        self, *, item_ids: list[int], telegram_message_id: int,
    ) -> None:
        if not item_ids:
            return
        placeholders = ",".join("?" for _ in item_ids)
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                f"UPDATE news_digest_items SET telegram_message_id = ? "
                f"WHERE id IN ({placeholders})",
                (telegram_message_id, *item_ids),
            )
            await db.commit()

    async def list_items_for_message(
        self, telegram_message_id: int,
    ) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM news_digest_items "
                "WHERE telegram_message_id = ? ORDER BY position ASC",
                (telegram_message_id,),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def upsert_rating(self, *, item_id: int, rating: str) -> None:
        if rating not in VALID_RATINGS:
            raise ValueError(f"invalid rating {rating!r}")
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT digest_date FROM news_digest_items WHERE id = ?",
                (item_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                raise KeyError(f"no digest item with id={item_id}")
            await db.execute(
                "INSERT INTO news_ratings "
                "(item_id, digest_date, rating, rated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(item_id) DO UPDATE SET "
                "rating = excluded.rating, rated_at = excluded.rated_at",
                (item_id, row["digest_date"], rating, _now_iso()),
            )
            await db.commit()

    async def get_rating(self, item_id: int) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM news_ratings WHERE item_id = ?", (item_id,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def recent_ratings(
        self, start: date, end: date,
    ) -> list[dict[str, Any]]:
        """All ratings whose digest_date is in [start, end] inclusive.

        Each row joins news_digest_items so the caller can build a prompt
        block keyed by category + title without a second query.
        """
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT r.item_id, r.digest_date, r.rating, r.rated_at, "
                "       i.category, i.title, i.source_path, i.url "
                "FROM news_ratings r "
                "JOIN news_digest_items i ON i.id = r.item_id "
                "WHERE r.digest_date BETWEEN ? AND ? "
                "ORDER BY r.rated_at DESC",
                (_date_key(start), _date_key(end)),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
