"""Pure logic for the speaker-resolution gate (no I/O).

A Plaud email transcript labels each speaker turn with either a real name
(Plaud recognised a person previously labelled in the Plaud app) or a
generic ``Speaker N`` / ``Speaker_N`` placeholder (Plaud did not recognise
them). See ADR-010 (speaker-resolution-gate).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, replace
from pathlib import Path

from pkm import TranscriptData, TranscriptSegment

from .models import RecordingJob

_GENERIC_SPEAKER = re.compile(r"^speaker[ _]?\d+$", re.IGNORECASE)


def is_unrecognised(label: str) -> bool:
    """True if `label` is a Plaud generic placeholder, not a real name."""
    return bool(_GENERIC_SPEAKER.match(label.strip()))


def unrecognised_speakers(transcript: TranscriptData) -> list[str]:
    """Distinct generic speaker labels, in first-seen order."""
    out: list[str] = []
    for label in transcript.speakers:
        if is_unrecognised(label) and label not in out:
            out.append(label)
    return out


SPEAKER_CALLBACK_PREFIX = "sr"
OTHER = "__other__"
IGNORE = "__ignore__"


def encode_choice(speaker_idx: int, choice: str) -> str:
    """Build callback_data for one keyboard button. <=64 bytes (names are
    truncated by the keyboard builder, not here)."""
    return f"{SPEAKER_CALLBACK_PREFIX}|{speaker_idx}|{choice}"


def decode_choice(data: str) -> tuple[int, str] | None:
    """Parse our callback_data. Returns None for tokens we don't own."""
    parts = data.split("|", 2)
    if len(parts) != 3 or parts[0] != SPEAKER_CALLBACK_PREFIX:
        return None
    try:
        idx = int(parts[1])
    except ValueError:
        return None
    return idx, parts[2]


def apply_speaker_names(
    transcript: TranscriptData, name_map: dict[str, str],
) -> TranscriptData:
    """Return a copy of `transcript` with speaker labels remapped.
    Labels absent from `name_map` are left as-is. Input is not mutated."""
    new_segments = [
        replace(s, speaker=name_map.get(s.speaker, s.speaker))
        for s in transcript.segments
    ]
    new_speakers = [name_map.get(sp, sp) for sp in transcript.speakers]
    return replace(transcript, speakers=new_speakers, segments=new_segments)


def serialize_job(job: RecordingJob) -> str:
    """Serialise a held RecordingJob to a JSON string for SQLite storage.

    ``infographic_path`` (a ``Path``) is stringified so it survives JSON
    round-trip; ``deserialize_job`` restores it back to ``Path``."""
    meta = dict(job.source_metadata)
    if isinstance(meta.get("infographic_path"), Path):
        meta["infographic_path"] = str(meta["infographic_path"])
    return json.dumps({
        "id": job.id,
        "recorded_at": job.recorded_at,
        "filename": job.filename,
        "source": job.source,
        "duration_ms": job.duration_ms,
        "transcript_data": asdict(job.transcript_data),
        "source_metadata": meta,
    })


def deserialize_job(blob: str) -> RecordingJob:
    """Rehydrate a RecordingJob from a JSON string produced by ``serialize_job``."""
    d = json.loads(blob)
    td = d["transcript_data"]
    transcript = TranscriptData(
        job_id=td["job_id"], recorded_at=td["recorded_at"],
        duration_seconds=td["duration_seconds"], speakers=list(td["speakers"]),
        segments=[TranscriptSegment(**s) for s in td["segments"]],
        full_text=td["full_text"],
    )
    meta = dict(d["source_metadata"])
    if "infographic_path" in meta and meta["infographic_path"] is not None:
        meta["infographic_path"] = Path(meta["infographic_path"])
    return RecordingJob(
        id=d["id"], recorded_at=d["recorded_at"], filename=d["filename"],
        source=d["source"], transcript_data=transcript,
        duration_ms=d["duration_ms"], source_metadata=meta,
    )
