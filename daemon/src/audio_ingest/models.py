from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from pkm import TranscriptData


@dataclass(frozen=True)
class RecordingJob:
    id: str
    recorded_at: str  # ISO 8601
    filename: str
    source: str
    transcript_data: TranscriptData
    duration_ms: int | None = None
    # Source-specific extras (e.g. plaud_summaries, infographic_path). Keeps
    # RecordingJob source-agnostic; only the source's extraction code knows
    # how to interpret the values.
    source_metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class StatusTracker(Protocol):
    async def update(self, status: str, **kwargs: str | None) -> None: ...
