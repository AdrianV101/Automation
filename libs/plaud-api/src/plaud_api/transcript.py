from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict


class TranscriptMetadata(TypedDict, total=False):
    language: str
    speaker_embeddings: dict[str, list[float]]
    speaker_mapping: dict[str, str]


@dataclass
class TranscriptSegment:
    start: float
    end: float
    speaker: str
    text: str


@dataclass
class Transcript:
    job_id: str
    recorded_at: str
    duration_seconds: float | None
    speakers: list[str]
    segments: list[TranscriptSegment]
    full_text: str
    raw_metadata: TranscriptMetadata = field(default_factory=dict)


def plaud_segments_to_transcript(
    segments: list[dict],
    job_id: str,
    recorded_at: str,
    duration_ms: int | None,
) -> Transcript:
    converted = [
        TranscriptSegment(
            start=seg["start_time"] / 1000,
            end=seg["end_time"] / 1000,
            speaker=seg["speaker"],
            text=seg["content"],
        )
        for seg in segments
    ]

    speakers = sorted({seg.speaker for seg in converted})

    full_text = "\n".join(f"[{seg.speaker}] {seg.text}" for seg in converted)

    duration_seconds = duration_ms / 1000 if duration_ms is not None else None

    return Transcript(
        job_id=job_id,
        recorded_at=recorded_at,
        duration_seconds=duration_seconds,
        speakers=speakers,
        segments=converted,
        full_text=full_text,
    )
