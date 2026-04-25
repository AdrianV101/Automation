from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from .client import PlaudClient, PlaudRecording
from .state import PlaudStateDB

log = logging.getLogger(__name__)


def sanitize_filename(name: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*]', "", name)
    sanitized = sanitized.replace(" ", "-")
    sanitized = re.sub(r"-+", "-", sanitized)
    sanitized = sanitized.strip("-.")
    return sanitized[:100]


def format_recording_filename(recording: PlaudRecording) -> str:
    # start_time is milliseconds since Unix epoch (divide by 1000 for seconds)
    date = datetime.fromtimestamp(
        recording.start_time / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d")
    name = sanitize_filename(recording.filename)
    return f"{date}_{name}.ogg"


async def download_new_recordings(
    plaud_client: PlaudClient,
    state_db: PlaudStateDB,
    download_dir: Path,
) -> tuple[list[tuple[PlaudRecording, list[dict]]], list[tuple[PlaudRecording, str]]]:
    """
    Fetch transcripts for new recordings that haven't been downloaded yet.

    Returns:
        Tuple of (successful_downloads, failed_downloads) where:
        - successful_downloads: list of (recording, transcript_segments) tuples
        - failed_downloads: list of (recording, error_message) tuples
    """
    recordings = await plaud_client.list_recordings(include_trash=False)
    log.debug("Found %d recordings from Plaud API", len(recordings))

    downloaded: list[tuple[PlaudRecording, list[dict]]] = []
    failed: list[tuple[PlaudRecording, str]] = []

    for recording in recordings:
        if await state_db.is_downloaded(recording.id):
            continue

        log.info("Fetching transcript for %s (%s)", recording.filename, recording.id)
        try:
            segments = await plaud_client.get_transcript(recording.id)
            transcript_json = json.dumps(segments)
            await state_db.mark_downloaded(
                recording_id=recording.id,
                filename=recording.filename,
                recorded_at=recording.start_time,
                duration_ms=recording.duration_ms,
                filesize=recording.filesize,
                local_path=Path("n/a"),
                transcript_data=transcript_json,
            )
            downloaded.append((recording, segments))
            log.info("Fetched transcript for %s (%d segments)", recording.filename, len(segments))
        except Exception as e:
            error_msg = str(e)
            log.error("Failed to fetch transcript for %s: %s", recording.id, error_msg)
            failed.append((recording, error_msg))

    return downloaded, failed
