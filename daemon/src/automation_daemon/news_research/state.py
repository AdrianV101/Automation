from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_research_runs (
    research_date TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    items_researched INTEGER,
    cost_usd REAL,
    turns_used INTEGER,
    error TEXT
);
"""

NEWS_RESEARCH_VALID_STATUSES = frozenset({
    "running",
    "completed",
    "skipped_no_master",
    "failed",
    "failed_notes_clobbered",
})

_TERMINAL_STATUSES = frozenset(NEWS_RESEARCH_VALID_STATUSES) - {"running"}

_UPDATABLE_COLUMNS = frozenset({
    "items_researched", "cost_usd", "turns_used", "error",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_key(d: date) -> str:
    return d.isoformat()


class NewsResearchStateDB:
    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.executescript(_SCHEMA)
            await db.commit()

    async def insert_run(self, research_date: date) -> None:
        """Insert a fresh 'running' row; replaces any existing row for the date."""
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO news_research_runs "
                "(research_date, status, started_at, completed_at, "
                " items_researched, cost_usd, turns_used, error) "
                "VALUES (?, 'running', ?, NULL, NULL, NULL, NULL, NULL)",
                (_date_key(research_date), _now_iso()),
            )
            await db.commit()

    async def get_run(self, research_date: date) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM news_research_runs WHERE research_date = ?",
                (_date_key(research_date),),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def update_run(
        self, research_date: date, status: str, **fields: Any,
    ) -> None:
        if status not in NEWS_RESEARCH_VALID_STATUSES:
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
        params.append(_date_key(research_date))
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute(
                f"UPDATE news_research_runs SET {', '.join(cols)} "
                f"WHERE research_date = ?",
                params,
            )
            await db.commit()
            if cur.rowcount == 0:
                raise KeyError(
                    f"no run row for research_date="
                    f"{research_date.isoformat()!r}",
                )
