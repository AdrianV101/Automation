from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

# Valid processing states
PROCESSING_STATES = (
    "downloaded",
    "extracting",
    "writing_pkm",
    "completed",
    "failed",
)


@dataclass
class DownloadedRecording:
    recording_id: str
    filename: str
    recorded_at: int
    duration_ms: int
    downloaded_at: str
    local_path: str
    filesize: int
    processing_status: str = "downloaded"
    processing_error: str | None = None
    transcript_path: str | None = None
    summary_path: str | None = None
    processed_at: str | None = None
    transcript_data: str | None = None


class PlaudStateDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS downloaded_recordings (
                    recording_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    recorded_at INTEGER NOT NULL,
                    duration_ms INTEGER,
                    downloaded_at TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    filesize INTEGER
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_downloaded_at
                ON downloaded_recordings(downloaded_at)
            """)
            # Migrate: rename plaud_id -> recording_id if old schema
            cursor = await db.execute("PRAGMA table_info(downloaded_recordings)")
            existing_columns = {row[1] for row in await cursor.fetchall()}

            if "plaud_id" in existing_columns:
                try:
                    await db.execute(
                        "ALTER TABLE downloaded_recordings RENAME COLUMN plaud_id TO recording_id"
                    )
                    log.info("Migrated: renamed column plaud_id -> recording_id")
                except Exception:
                    log.exception(
                        "Failed to rename plaud_id -> recording_id in downloaded_recordings. "
                        "SQLite 3.25+ required for RENAME COLUMN."
                    )
                    raise

            # Migrate: add processing columns if missing
            new_columns = {
                "processing_status": "TEXT DEFAULT 'downloaded'",
                "processing_error": "TEXT",
                "transcript_path": "TEXT",
                "summary_path": "TEXT",
                "processed_at": "TEXT",
                "transcript_data": "TEXT",
            }
            for col_name, col_def in new_columns.items():
                if col_name not in existing_columns:
                    await db.execute(
                        f"ALTER TABLE downloaded_recordings ADD COLUMN {col_name} {col_def}"
                    )
                    log.info("Migrated: added column %s", col_name)
            # Settings table for persistent key-value pairs
            await db.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            await db.commit()

    async def get_setting(self, key: str) -> str | None:
        """Get a persistent setting value by key."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        """Set a persistent setting value."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
            await db.commit()

    async def is_downloaded(self, recording_id: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM downloaded_recordings WHERE recording_id = ?",
                (recording_id,),
            )
            row = await cursor.fetchone()
            return row is not None

    async def mark_downloaded(
        self,
        recording_id: str,
        filename: str,
        recorded_at: int,
        duration_ms: int,
        filesize: int,
        local_path: Path,
        transcript_data: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO downloaded_recordings
                (recording_id, filename, recorded_at, duration_ms, downloaded_at,
                 local_path, filesize, processing_status, transcript_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'downloaded', ?)
                """,
                (
                    recording_id,
                    filename,
                    recorded_at,
                    duration_ms,
                    now,
                    str(local_path),
                    filesize,
                    transcript_data,
                ),
            )
            await db.commit()

    async def update_processing_status(
        self,
        recording_id: str,
        status: str,
        error: str | None = None,
        transcript_path: str | None = None,
        summary_path: str | None = None,
    ) -> None:
        if status not in PROCESSING_STATES:
            raise ValueError(
                f"Invalid processing status {status!r}, "
                f"must be one of {PROCESSING_STATES}"
            )
        async with aiosqlite.connect(self.db_path) as db:
            sets = ["processing_status = ?"]
            params: list = [status]
            if error is not None:
                sets.append("processing_error = ?")
                params.append(error)
            if transcript_path is not None:
                sets.append("transcript_path = ?")
                params.append(transcript_path)
            if summary_path is not None:
                sets.append("summary_path = ?")
                params.append(summary_path)
            if status in ("completed", "failed"):
                sets.append("processed_at = ?")
                params.append(datetime.now(timezone.utc).isoformat())
            params.append(recording_id)
            await db.execute(
                f"UPDATE downloaded_recordings SET {', '.join(sets)} WHERE recording_id = ?",
                params,
            )
            await db.commit()

    async def get_pending_processing(self) -> list[DownloadedRecording]:
        """Get recordings that need processing (downloaded but not completed/failed)."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT recording_id, filename, recorded_at, duration_ms,
                       downloaded_at, local_path, filesize,
                       processing_status, processing_error,
                       transcript_path, summary_path, processed_at,
                       transcript_data
                FROM downloaded_recordings
                WHERE processing_status NOT IN ('completed', 'failed')
                   OR processing_status IS NULL
                ORDER BY downloaded_at ASC
                """,
            )
            rows = await cursor.fetchall()
            return [self._row_to_recording(row) for row in rows]

    async def get_completed_recordings(self) -> list[DownloadedRecording]:
        """Get recordings that finished processing successfully."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT recording_id, filename, recorded_at, duration_ms,
                       downloaded_at, local_path, filesize,
                       processing_status, processing_error,
                       transcript_path, summary_path, processed_at,
                       transcript_data
                FROM downloaded_recordings
                WHERE processing_status = 'completed'
                ORDER BY recorded_at ASC
                """,
            )
            rows = await cursor.fetchall()
            return [self._row_to_recording(row) for row in rows]

    async def get_download_count(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM downloaded_recordings"
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_recent_downloads(self, limit: int = 10) -> list[DownloadedRecording]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT recording_id, filename, recorded_at, duration_ms,
                       downloaded_at, local_path, filesize,
                       processing_status, processing_error,
                       transcript_path, summary_path, processed_at,
                       transcript_data
                FROM downloaded_recordings
                ORDER BY downloaded_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_recording(row) for row in rows]

    @staticmethod
    def _row_to_recording(row: tuple) -> DownloadedRecording:
        return DownloadedRecording(
            recording_id=row[0],
            filename=row[1],
            recorded_at=row[2],
            duration_ms=row[3],
            downloaded_at=row[4],
            local_path=row[5],
            filesize=row[6],
            processing_status=row[7] or "downloaded",
            processing_error=row[8],
            transcript_path=row[9],
            summary_path=row[10],
            processed_at=row[11],
            transcript_data=row[12] if len(row) > 12 else None,
        )
