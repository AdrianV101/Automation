from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_weekly_runs (
    iso_week TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    threads_written INTEGER,
    entities_enriched INTEGER,
    cost_usd REAL,
    turns_used INTEGER,
    error TEXT
);
"""

NEWS_WEEKLY_VALID_STATUSES = frozenset({
    "running",
    "completed",
    "skipped_insufficient_corpus",
    "failed",
    "failed_notes_clobbered",
})

_TERMINAL_STATUSES = frozenset(NEWS_WEEKLY_VALID_STATUSES) - {"running"}

_UPDATABLE_COLUMNS = frozenset({
    "threads_written", "entities_enriched", "cost_usd", "turns_used", "error",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class NewsWeeklyStateDB:
    """Run-history for the weekly pattern agent. Keyed by ISO-week 'YYYY-WW'.

    Same status-machine + retry shape as NewsResearchStateDB; shares the
    daemon's single state-DB file (one extra table, WAL mode).
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.executescript(_SCHEMA)
            await db.commit()

    async def insert_run(self, iso_week: str) -> None:
        """Insert a fresh 'running' row; replaces any existing row."""
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO news_weekly_runs "
                "(iso_week, status, started_at, completed_at, "
                " threads_written, entities_enriched, cost_usd, "
                " turns_used, error) "
                "VALUES (?, 'running', ?, NULL, NULL, NULL, NULL, NULL, NULL)",
                (iso_week, _now_iso()),
            )
            await db.commit()

    async def get_run(self, iso_week: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM news_weekly_runs WHERE iso_week = ?",
                (iso_week,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def update_run(
        self, iso_week: str, status: str, **fields: Any,
    ) -> None:
        if status not in NEWS_WEEKLY_VALID_STATUSES:
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
        params.append(iso_week)
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute(
                f"UPDATE news_weekly_runs SET {', '.join(cols)} "
                f"WHERE iso_week = ?",
                params,
            )
            await db.commit()
            if cur.rowcount == 0:
                raise KeyError(f"no run row for iso_week={iso_week!r}")

    async def attempted_weeks(self, candidates: set[str]) -> set[str]:
        """Subset of `candidates` that already have a row (any status).

        Backfill skips weeks with a row — terminally-failed weeks are NOT
        re-fired on restart (mirrors the daily master's
        get_unattempted_in_window contract: scheduled fire retries, not
        boot).
        """
        if not candidates:
            return set()
        placeholders = ",".join("?" * len(candidates))
        async with aiosqlite.connect(self._path) as db:
            async with db.execute(
                f"SELECT iso_week FROM news_weekly_runs "
                f"WHERE iso_week IN ({placeholders})",
                tuple(candidates),
            ) as cur:
                return {r[0] for r in await cur.fetchall()}
