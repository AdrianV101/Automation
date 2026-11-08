"""Readiness trigger: decide when a Plaud recording's speaker labels have settled."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from datetime import datetime, timedelta


@dataclass(frozen=True)
class TrackState:
    ready_at: datetime
    speaker_map: dict[str, str]
    last_label_change_at: datetime | None


_GENERIC_RE = re.compile(r"^speaker[ _]?\d+$", re.IGNORECASE)


def is_generic_speaker(label: str) -> bool:
    return _GENERIC_RE.match(label.strip()) is not None


def has_generic(speaker_map: Mapping[str, str]) -> bool:
    return any(is_generic_speaker(v) for v in speaker_map.values())


def decide(
    prev: TrackState | None,
    current_map: dict[str, str],
    now: datetime,
    *,
    quiet_s: int,
    fallback_s: int,
) -> tuple[bool, TrackState]:
    if prev is None:
        return (not has_generic(current_map)), TrackState(now, current_map, None)

    state = prev
    if current_map != prev.speaker_map:
        state = TrackState(prev.ready_at, current_map, now)

    changed_at = state.last_label_change_at
    if not has_generic(current_map):
        ingest = True
    elif changed_at is not None:
        ingest = now - changed_at >= timedelta(seconds=quiet_s)
    else:
        ingest = now - state.ready_at >= timedelta(seconds=fallback_s)
    return ingest, state
