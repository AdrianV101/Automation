from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TranscriptSegment:
    start: float
    end: float
    speaker: str
    text: str


@dataclass
class TranscriptData:
    job_id: str
    recorded_at: str
    duration_seconds: float | None
    speakers: list[str]
    segments: list[TranscriptSegment]
    full_text: str
