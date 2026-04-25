from pathlib import Path

import aiosqlite
import pytest

from plaud_api.state import PlaudStateDB


class TestPlaudStateDB:
    @pytest.mark.asyncio
    async def test_init_creates_table(self, tmp_path):
        db_path = tmp_path / "test.db"
        state_db = PlaudStateDB(db_path)

        await state_db.init_db()

        assert db_path.exists()

    @pytest.mark.asyncio
    async def test_is_downloaded_false_for_new(self, tmp_path):
        db_path = tmp_path / "test.db"
        state_db = PlaudStateDB(db_path)
        await state_db.init_db()

        result = await state_db.is_downloaded("nonexistent")

        assert result is False

    @pytest.mark.asyncio
    async def test_mark_downloaded_and_check(self, tmp_path):
        db_path = tmp_path / "test.db"
        state_db = PlaudStateDB(db_path)
        await state_db.init_db()

        await state_db.mark_downloaded(
            recording_id="abc123",
            filename="Test Recording",
            recorded_at=1770000000000,
            duration_ms=60000,
            filesize=100000,
            local_path=Path("/downloads/test.ogg"),
        )

        assert await state_db.is_downloaded("abc123") is True
        assert await state_db.is_downloaded("other_id") is False

    @pytest.mark.asyncio
    async def test_get_download_count(self, tmp_path):
        db_path = tmp_path / "test.db"
        state_db = PlaudStateDB(db_path)
        await state_db.init_db()

        assert await state_db.get_download_count() == 0

        await state_db.mark_downloaded(
            recording_id="abc123",
            filename="Test",
            recorded_at=1770000000000,
            duration_ms=60000,
            filesize=100000,
            local_path=Path("/test.ogg"),
        )

        assert await state_db.get_download_count() == 1

    @pytest.mark.asyncio
    async def test_get_recent_downloads(self, tmp_path):
        db_path = tmp_path / "test.db"
        state_db = PlaudStateDB(db_path)
        await state_db.init_db()

        for i in range(3):
            await state_db.mark_downloaded(
                recording_id=f"rec{i}",
                filename=f"Recording {i}",
                recorded_at=1770000000000 + i * 1000,
                duration_ms=60000,
                filesize=100000,
                local_path=Path(f"/downloads/rec{i}.ogg"),
            )

        recent = await state_db.get_recent_downloads(limit=2)

        assert len(recent) == 2
        assert recent[0].recording_id == "rec2"
        assert recent[1].recording_id == "rec1"

    @pytest.mark.asyncio
    async def test_mark_downloaded_updates_existing(self, tmp_path):
        db_path = tmp_path / "test.db"
        state_db = PlaudStateDB(db_path)
        await state_db.init_db()

        await state_db.mark_downloaded(
            recording_id="abc123",
            filename="Test",
            recorded_at=1770000000000,
            duration_ms=60000,
            filesize=100000,
            local_path=Path("/old/path.ogg"),
        )
        await state_db.mark_downloaded(
            recording_id="abc123",
            filename="Test",
            recorded_at=1770000000000,
            duration_ms=60000,
            filesize=100000,
            local_path=Path("/new/path.ogg"),
        )

        assert await state_db.get_download_count() == 1

        recent = await state_db.get_recent_downloads(limit=1)
        assert recent[0].local_path == "/new/path.ogg"

    @pytest.mark.asyncio
    async def test_update_processing_status(self, tmp_path):
        db_path = tmp_path / "test.db"
        state_db = PlaudStateDB(db_path)
        await state_db.init_db()

        await state_db.mark_downloaded(
            recording_id="abc123",
            filename="Test",
            recorded_at=1770000000000,
            duration_ms=60000,
            filesize=100000,
            local_path=Path("/test.ogg"),
        )

        await state_db.update_processing_status("abc123", "extracting")

        recent = await state_db.get_recent_downloads(limit=1)
        assert recent[0].processing_status == "extracting"

    @pytest.mark.asyncio
    async def test_update_processing_status_with_error(self, tmp_path):
        db_path = tmp_path / "test.db"
        state_db = PlaudStateDB(db_path)
        await state_db.init_db()

        await state_db.mark_downloaded(
            recording_id="abc123",
            filename="Test",
            recorded_at=1770000000000,
            duration_ms=60000,
            filesize=100000,
            local_path=Path("/test.ogg"),
        )
        await state_db.update_processing_status(
            "abc123", "failed", error="GPU OOM"
        )

        recent = await state_db.get_recent_downloads(limit=1)
        assert recent[0].processing_status == "failed"
        assert recent[0].processing_error == "GPU OOM"
        assert recent[0].processed_at is not None

    @pytest.mark.asyncio
    async def test_update_processing_status_completed_sets_paths(self, tmp_path):
        db_path = tmp_path / "test.db"
        state_db = PlaudStateDB(db_path)
        await state_db.init_db()

        await state_db.mark_downloaded(
            recording_id="abc123",
            filename="Test",
            recorded_at=1770000000000,
            duration_ms=60000,
            filesize=100000,
            local_path=Path("/test.ogg"),
        )
        await state_db.update_processing_status(
            "abc123", "completed",
            transcript_path="/pkm/transcript.md",
            summary_path="/pkm/summary.md",
        )

        recent = await state_db.get_recent_downloads(limit=1)
        assert recent[0].processing_status == "completed"
        assert recent[0].transcript_path == "/pkm/transcript.md"
        assert recent[0].summary_path == "/pkm/summary.md"
        assert recent[0].processed_at is not None

    @pytest.mark.asyncio
    async def test_get_pending_processing(self, tmp_path):
        db_path = tmp_path / "test.db"
        state_db = PlaudStateDB(db_path)
        await state_db.init_db()

        # Create 3 recordings with different statuses
        for i, status in enumerate(["downloaded", "completed", "extracting"]):
            await state_db.mark_downloaded(
                recording_id=f"rec{i}",
                filename=f"Recording {i}",
                recorded_at=1770000000000 + i * 1000,
                duration_ms=60000,
                filesize=100000,
                local_path=Path(f"/downloads/rec{i}.ogg"),
            )
            if status != "downloaded":
                await state_db.update_processing_status(f"rec{i}", status)

        pending = await state_db.get_pending_processing()

        # Should return downloaded and extracting, but NOT completed
        pending_ids = [p.recording_id for p in pending]
        assert "rec0" in pending_ids  # downloaded
        assert "rec2" in pending_ids  # extracting (stuck)
        assert "rec1" not in pending_ids  # completed

    @pytest.mark.asyncio
    async def test_get_completed_recordings(self, tmp_path):
        db_path = tmp_path / "test.db"
        state_db = PlaudStateDB(db_path)
        await state_db.init_db()

        # Create 3 recordings with different statuses
        for i, status in enumerate(["downloaded", "completed", "extracting"]):
            await state_db.mark_downloaded(
                recording_id=f"rec{i}",
                filename=f"Recording {i}",
                recorded_at=1770000000000 + i * 1000,
                duration_ms=60000,
                filesize=100000,
                local_path=Path(f"/downloads/rec{i}.ogg"),
            )
            if status != "downloaded":
                await state_db.update_processing_status(f"rec{i}", status)

        completed = await state_db.get_completed_recordings()

        # Should return only the completed recording
        assert len(completed) == 1
        assert completed[0].recording_id == "rec1"
        assert completed[0].processing_status == "completed"

    @pytest.mark.asyncio
    async def test_update_processing_status_rejects_invalid(self, tmp_path):
        db_path = tmp_path / "test.db"
        state_db = PlaudStateDB(db_path)
        await state_db.init_db()

        await state_db.mark_downloaded(
            recording_id="abc123",
            filename="Test",
            recorded_at=1770000000000,
            duration_ms=60000,
            filesize=100000,
            local_path=Path("/test.ogg"),
        )

        with pytest.raises(ValueError, match="Invalid processing status"):
            await state_db.update_processing_status("abc123", "bogus_status")

    @pytest.mark.asyncio
    async def test_migration_adds_processing_columns(self, tmp_path):
        """Verify migration adds processing columns to existing DB without them."""
        db_path = tmp_path / "test.db"

        # Create old-schema DB manually (with recording_id but no processing columns)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                CREATE TABLE downloaded_recordings (
                    recording_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    recorded_at INTEGER NOT NULL,
                    duration_ms INTEGER,
                    downloaded_at TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    filesize INTEGER
                )
            """)
            await db.execute(
                "INSERT INTO downloaded_recordings VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("old_rec", "Old Recording", 1770000000000, 60000, "2025-01-01", "/old.ogg", 100000),
            )
            await db.commit()

        # Now init_db should add missing columns
        state_db = PlaudStateDB(db_path)
        await state_db.init_db()

        # Should be able to read with new columns
        recent = await state_db.get_recent_downloads(limit=1)
        assert recent[0].recording_id == "old_rec"
        assert recent[0].processing_status == "downloaded"

        # Should be able to update processing status
        await state_db.update_processing_status("old_rec", "completed")
        recent = await state_db.get_recent_downloads(limit=1)
        assert recent[0].processing_status == "completed"

    @pytest.mark.asyncio
    async def test_migration_renames_plaud_id_to_recording_id(self, tmp_path):
        """Create old-schema DB with plaud_id column, run init_db, verify recording_id works."""
        db_path = tmp_path / "test.db"

        # Create old-schema DB with plaud_id as PK
        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                CREATE TABLE downloaded_recordings (
                    plaud_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    recorded_at INTEGER NOT NULL,
                    duration_ms INTEGER,
                    downloaded_at TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    filesize INTEGER,
                    processing_status TEXT DEFAULT 'downloaded',
                    processing_error TEXT,
                    transcript_path TEXT,
                    summary_path TEXT,
                    processed_at TEXT
                )
            """)
            await db.execute(
                """INSERT INTO downloaded_recordings
                   (plaud_id, filename, recorded_at, duration_ms, downloaded_at,
                    local_path, filesize, processing_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'downloaded')""",
                ("old_plaud_rec", "Old Recording", 1770000000000, 60000,
                 "2025-01-01T00:00:00", "/old.ogg", 100000),
            )
            await db.commit()

        # Run init_db which should rename plaud_id -> recording_id
        state_db = PlaudStateDB(db_path)
        await state_db.init_db()

        # Should be queryable via recording_id
        assert await state_db.is_downloaded("old_plaud_rec") is True

        recent = await state_db.get_recent_downloads(limit=1)
        assert recent[0].recording_id == "old_plaud_rec"
        assert recent[0].processing_status == "downloaded"

        # Should be able to mark new downloads
        await state_db.mark_downloaded(
            recording_id="new_rec",
            filename="New Recording",
            recorded_at=1770000000000,
            duration_ms=30000,
            filesize=50000,
            local_path=Path("/new.ogg"),
        )
        assert await state_db.is_downloaded("new_rec") is True
        assert await state_db.get_download_count() == 2


class TestSettings:
    @pytest.mark.asyncio
    async def test_get_setting_returns_none_for_missing(self, tmp_path):
        state_db = PlaudStateDB(tmp_path / "test.db")
        await state_db.init_db()

        assert await state_db.get_setting("nonexistent") is None

    @pytest.mark.asyncio
    async def test_set_and_get_setting(self, tmp_path):
        state_db = PlaudStateDB(tmp_path / "test.db")
        await state_db.init_db()

        await state_db.set_setting("pipeline_thread_id", "12345")

        assert await state_db.get_setting("pipeline_thread_id") == "12345"

    @pytest.mark.asyncio
    async def test_set_setting_overwrites(self, tmp_path):
        state_db = PlaudStateDB(tmp_path / "test.db")
        await state_db.init_db()

        await state_db.set_setting("key", "old_value")
        await state_db.set_setting("key", "new_value")

        assert await state_db.get_setting("key") == "new_value"

    @pytest.mark.asyncio
    async def test_settings_survive_reinit(self, tmp_path):
        """Settings persist across init_db calls (simulating restart)."""
        db_path = tmp_path / "test.db"
        state_db = PlaudStateDB(db_path)
        await state_db.init_db()
        await state_db.set_setting("pipeline_thread_id", "99999")

        # Simulate restart -- new instance, same DB file
        state_db2 = PlaudStateDB(db_path)
        await state_db2.init_db()

        assert await state_db2.get_setting("pipeline_thread_id") == "99999"


class TestTranscriptData:
    @pytest.mark.asyncio
    async def test_mark_downloaded_with_transcript_data(self, tmp_path):
        db_path = tmp_path / "test.db"
        state_db = PlaudStateDB(db_path)
        await state_db.init_db()

        transcript_json = '[{"speaker": "A", "content": "Hello"}]'
        await state_db.mark_downloaded(
            recording_id="abc123",
            filename="Test",
            recorded_at=1770000000000,
            duration_ms=60000,
            filesize=100000,
            local_path=Path("n/a"),
            transcript_data=transcript_json,
        )

        recent = await state_db.get_recent_downloads(limit=1)
        assert recent[0].transcript_data == transcript_json

    @pytest.mark.asyncio
    async def test_mark_downloaded_without_transcript_data(self, tmp_path):
        db_path = tmp_path / "test.db"
        state_db = PlaudStateDB(db_path)
        await state_db.init_db()

        await state_db.mark_downloaded(
            recording_id="abc123",
            filename="Test",
            recorded_at=1770000000000,
            duration_ms=60000,
            filesize=100000,
            local_path=Path("/test.ogg"),
        )

        recent = await state_db.get_recent_downloads(limit=1)
        assert recent[0].transcript_data is None

    @pytest.mark.asyncio
    async def test_migration_adds_transcript_data_column(self, tmp_path):
        """Verify migration adds transcript_data column to existing DB without it."""
        db_path = tmp_path / "test.db"

        # Create old-schema DB (without transcript_data column)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                CREATE TABLE downloaded_recordings (
                    recording_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    recorded_at INTEGER NOT NULL,
                    duration_ms INTEGER,
                    downloaded_at TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    filesize INTEGER,
                    processing_status TEXT DEFAULT 'downloaded',
                    processing_error TEXT,
                    transcript_path TEXT,
                    summary_path TEXT,
                    processed_at TEXT
                )
            """)
            await db.execute(
                """INSERT INTO downloaded_recordings
                   (recording_id, filename, recorded_at, duration_ms, downloaded_at,
                    local_path, filesize, processing_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'downloaded')""",
                ("old_rec", "Old Recording", 1770000000000, 60000,
                 "2025-01-01T00:00:00", "/old.ogg", 100000),
            )
            await db.commit()

        # Now init_db should add transcript_data column
        state_db = PlaudStateDB(db_path)
        await state_db.init_db()

        # Should be able to read with new column (defaults to None)
        recent = await state_db.get_recent_downloads(limit=1)
        assert recent[0].recording_id == "old_rec"
        assert recent[0].transcript_data is None

        # Should be able to store transcript_data
        await state_db.mark_downloaded(
            recording_id="new_rec",
            filename="New",
            recorded_at=1770000000000,
            duration_ms=30000,
            filesize=50000,
            local_path=Path("n/a"),
            transcript_data='[{"speaker":"A","content":"Hi"}]',
        )
        recent = await state_db.get_recent_downloads(limit=1)
        assert recent[0].transcript_data == '[{"speaker":"A","content":"Hi"}]'
