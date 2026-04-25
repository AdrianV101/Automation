import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from plaud_api.client import PlaudRecording
from plaud_api.downloader import (
    download_new_recordings,
    format_recording_filename,
    sanitize_filename,
)


class TestSanitizeFilename:
    def test_removes_special_chars(self):
        assert sanitize_filename('file<>:"/\\|?*name') == "filename"

    def test_spaces_to_dashes(self):
        assert sanitize_filename("my file name") == "my-file-name"

    def test_collapses_multiple_dashes(self):
        assert sanitize_filename("my   file   name") == "my-file-name"
        assert sanitize_filename("my---file---name") == "my-file-name"

    def test_strips_leading_trailing_dashes_and_dots(self):
        assert sanitize_filename("..file..") == "file"
        assert sanitize_filename("--file--") == "file"

    def test_truncates_long_names(self):
        long_name = "a" * 150
        result = sanitize_filename(long_name)
        assert len(result) == 100
        assert result == "a" * 100

    def test_complex_example(self):
        # Note: & is allowed in filenames, only <>:"/\|?* are removed
        assert sanitize_filename('Meeting: "Bob & Carol" | 2024') == "Meeting-Bob-&-Carol-2024"


class TestFormatRecordingFilename:
    def test_formats_with_date_and_name(self):
        # start_time is milliseconds since Unix epoch
        recording = PlaudRecording(
            id="abc123",
            filename="Weekly Sync",
            fullname="abc123.ogg",
            duration_ms=60000,
            start_time=1704067200000,  # 2024-01-01 00:00:00 UTC
            end_time=1704067260000,
            is_trash=False,
            filesize=100000,
            serial_number="DEVICE123",
        )
        result = format_recording_filename(recording)
        assert result == "2024-01-01_Weekly-Sync.ogg"

    def test_sanitizes_filename(self):
        recording = PlaudRecording(
            id="abc123",
            filename='Meeting: "Important" | Notes',
            fullname="abc123.ogg",
            duration_ms=60000,
            start_time=1704067200000,  # 2024-01-01 00:00:00 UTC
            end_time=1704067260000,
            is_trash=False,
            filesize=100000,
            serial_number="DEVICE123",
        )
        result = format_recording_filename(recording)
        assert result == "2024-01-01_Meeting-Important-Notes.ogg"


class TestDownloadNewRecordings:
    @pytest.fixture
    def mock_plaud_client(self):
        client = AsyncMock()
        return client

    @pytest.fixture
    def mock_state_db(self):
        db = AsyncMock()
        db.is_downloaded = AsyncMock(return_value=False)
        return db

    @pytest.fixture
    def sample_recording(self):
        return PlaudRecording(
            id="abc123",
            filename="Test Recording",
            fullname="abc123.ogg",
            duration_ms=60000,
            start_time=1704067200000,
            end_time=1704067260000,
            is_trash=False,
            filesize=100000,
            serial_number="DEVICE123",
        )

    @pytest.fixture
    def sample_segments(self):
        return [
            {"speaker": "A", "content": "Hello", "start_time": 0, "end_time": 5000},
            {"speaker": "B", "content": "Hi there", "start_time": 5000, "end_time": 10000},
        ]

    @pytest.mark.asyncio
    async def test_skips_already_downloaded(
        self, mock_plaud_client, mock_state_db, sample_recording, tmp_path
    ):
        mock_plaud_client.list_recordings.return_value = [sample_recording]
        mock_state_db.is_downloaded.return_value = True

        downloaded, failed = await download_new_recordings(
            mock_plaud_client, mock_state_db, tmp_path
        )

        assert downloaded == []
        assert failed == []
        mock_plaud_client.get_transcript.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetches_transcript_for_new_recording(
        self, mock_plaud_client, mock_state_db, sample_recording, sample_segments, tmp_path
    ):
        mock_plaud_client.list_recordings.return_value = [sample_recording]
        mock_plaud_client.get_transcript.return_value = sample_segments

        downloaded, failed = await download_new_recordings(
            mock_plaud_client, mock_state_db, tmp_path
        )

        assert len(downloaded) == 1
        assert downloaded[0][0] == sample_recording
        assert downloaded[0][1] == sample_segments
        assert failed == []
        mock_state_db.mark_downloaded.assert_called_once_with(
            recording_id="abc123",
            filename="Test Recording",
            recorded_at=1704067200000,
            duration_ms=60000,
            filesize=100000,
            local_path=Path("n/a"),
            transcript_data=json.dumps(sample_segments),
        )

    @pytest.mark.asyncio
    async def test_continues_on_failure(
        self, mock_plaud_client, mock_state_db, tmp_path
    ):
        recording1 = PlaudRecording(
            id="abc123",
            filename="Recording 1",
            fullname="abc123.ogg",
            duration_ms=60000,
            start_time=1704067200000,
            end_time=1704067260000,
            is_trash=False,
            filesize=100000,
            serial_number="DEVICE123",
        )
        recording2 = PlaudRecording(
            id="def456",
            filename="Recording 2",
            fullname="def456.ogg",
            duration_ms=60000,
            start_time=1704067300000,
            end_time=1704067360000,
            is_trash=False,
            filesize=100000,
            serial_number="DEVICE123",
        )
        segments = [{"speaker": "A", "content": "Hi", "start_time": 0, "end_time": 1000}]
        mock_plaud_client.list_recordings.return_value = [recording1, recording2]
        mock_plaud_client.get_transcript.side_effect = [
            Exception("Network error"),
            segments,
        ]

        downloaded, failed = await download_new_recordings(
            mock_plaud_client, mock_state_db, tmp_path
        )

        assert len(downloaded) == 1
        assert downloaded[0][0] == recording2
        assert downloaded[0][1] == segments
        assert len(failed) == 1
        assert failed[0][0] == recording1
        assert "Network error" in failed[0][1]

    @pytest.mark.asyncio
    async def test_returns_failures_with_error_messages(
        self, mock_plaud_client, mock_state_db, sample_recording, tmp_path
    ):
        mock_plaud_client.list_recordings.return_value = [sample_recording]
        mock_plaud_client.get_transcript.side_effect = Exception("Connection timeout")

        downloaded, failed = await download_new_recordings(
            mock_plaud_client, mock_state_db, tmp_path
        )

        assert downloaded == []
        assert len(failed) == 1
        assert failed[0][0] == sample_recording
        assert failed[0][1] == "Connection timeout"
