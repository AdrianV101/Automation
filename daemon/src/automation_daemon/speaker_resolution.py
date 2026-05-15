"""Pure logic for the speaker-resolution gate (no I/O).

A Plaud email transcript labels each speaker turn with either a real name
(Plaud recognised a person previously labelled in the Plaud app) or a
generic ``Speaker N`` / ``Speaker_N`` placeholder (Plaud did not recognise
them). See ADR-010 (speaker-resolution-gate).
"""
from __future__ import annotations

import re

from pkm import TranscriptData

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
