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
    "completed_with_skips",
    "skipped_empty",
    "failed",
    # Retained so historical rows written before the skip-gate fix stay
    # readable; nothing writes this status after the fix.
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

    async def update_run(
        self, target_date: date, status: str, **fields: Any,
    ) -> None:
        if status not in NEWS_DAILY_MASTER_VALID_STATUSES:
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
        params.append(_date_key(target_date))
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute(
                f"UPDATE news_daily_master_runs SET {', '.join(cols)} "
                f"WHERE target_date = ?",
                params,
            )
            await db.commit()
            if cur.rowcount == 0:
                raise KeyError(f"no run row for target_date={target_date.isoformat()!r}")

    async def get_last_completed(self) -> date | None:
        """Most recent target_date with a successful run.

        'completed', 'completed_with_skips' and 'skipped_empty' all count:
        a skip-day is a fully-written master doc (success), and skipped_empty
        is a successful 'no items to digest' decision — neither is a failure.
        """
        async with aiosqlite.connect(self._path) as db:
            async with db.execute(
                "SELECT target_date FROM news_daily_master_runs "
                "WHERE status IN "
                "('completed', 'completed_with_skips', 'skipped_empty') "
                "ORDER BY target_date DESC LIMIT 1",
            ) as cur:
                row = await cur.fetchone()
                if row is None:
                    return None
                return date.fromisoformat(row[0])

    async def get_unattempted_in_window(
        self, start: date, end: date,
    ) -> list[date]:
        """Dates in [start, end] (inclusive) with NO row in the runs table.

        Returned in ascending order. Used for backfill on daemon boot:
        terminally-failed dates have a row and are NOT re-fired here (the
        scheduled 06:00 fire retries 'yesterday' once per day via
        run_once → INSERT OR REPLACE). Permanent retry-on-restart would mask
        real failures and burn API quota.
        """
        if start > end:
            return []
        async with aiosqlite.connect(self._path) as db:
            async with db.execute(
                "SELECT target_date FROM news_daily_master_runs "
                "WHERE target_date BETWEEN ? AND ?",
                (_date_key(start), _date_key(end)),
            ) as cur:
                attempted = {date.fromisoformat(r[0]) for r in await cur.fetchall()}
        result: list[date] = []
        cur_date = start
        while cur_date <= end:
            if cur_date not in attempted:
                result.append(cur_date)
            cur_date = date.fromordinal(cur_date.toordinal() + 1)
        return result
