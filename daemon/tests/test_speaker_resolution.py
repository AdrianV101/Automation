from __future__ import annotations

from pkm import TranscriptData, TranscriptSegment

from automation_daemon.speaker_resolution import unrecognised_speakers


def _td(speakers: list[str]) -> TranscriptData:
    return TranscriptData(
        job_id="m1", recorded_at="2026-05-15T10:00:00+00:00",
        duration_seconds=10.0, speakers=speakers,
        segments=[TranscriptSegment(0.0, 1.0, sp, "hi") for sp in speakers],
        full_text="hi",
    )


def test_generic_labels_are_unrecognised() -> None:
    assert unrecognised_speakers(_td(["Speaker 1", "Speaker 2"])) == ["Speaker 1", "Speaker 2"]


def test_underscore_variant_is_unrecognised() -> None:
    assert unrecognised_speakers(_td(["Speaker_1", "Speaker_3"])) == ["Speaker_1", "Speaker_3"]


def test_real_names_pass_through() -> None:
    assert unrecognised_speakers(_td(["Alice", "Bob"])) == []


def test_mixed_only_returns_unknowns() -> None:
    assert unrecognised_speakers(_td(["Alice", "Speaker 2"])) == ["Speaker 2"]


def test_case_insensitive_and_preserves_order() -> None:
    assert unrecognised_speakers(_td(["speaker 5", "Carol", "Speaker 1"])) == ["speaker 5", "Speaker 1"]


import pytest

from automation_daemon.speaker_resolution import (
    SPEAKER_CALLBACK_PREFIX, encode_choice, decode_choice,
)


def test_prefix_is_distinct_from_digest() -> None:
    assert SPEAKER_CALLBACK_PREFIX == "sr"
    assert not SPEAKER_CALLBACK_PREFIX.startswith("nr")


@pytest.mark.parametrize("idx,choice", [(0, "Alice"), (2, "__other__"), (1, "__ignore__")])
def test_encode_decode_roundtrip(idx: int, choice: str) -> None:
    token = encode_choice(idx, choice)
    assert token.startswith("sr|")
    assert len(token.encode()) <= 64
    assert decode_choice(token) == (idx, choice)


def test_decode_rejects_foreign_token() -> None:
    assert decode_choice("nr:5:foo") is None
    assert decode_choice("garbage") is None


from automation_daemon.speaker_resolution import apply_speaker_names


def test_apply_substitutes_speakers_and_segments() -> None:
    td = _td(["Speaker 1", "Speaker 2"])
    out = apply_speaker_names(td, {"Speaker 1": "Alice", "Speaker 2": "Bob"})
    assert out.speakers == ["Alice", "Bob"]
    assert [s.speaker for s in out.segments] == ["Alice", "Bob"]
    # original is not mutated
    assert td.speakers == ["Speaker 1", "Speaker 2"]


def test_apply_leaves_unmapped_labels_unchanged() -> None:
    td = _td(["Speaker 1", "Alice"])
    out = apply_speaker_names(td, {"Speaker 1": "Bob"})
    assert out.speakers == ["Bob", "Alice"]


def test_apply_empty_map_is_identity_copy() -> None:
    td = _td(["Speaker 1"])
    out = apply_speaker_names(td, {})
    assert out.speakers == ["Speaker 1"]
    assert out is not td


from pathlib import Path

from automation_daemon.models import RecordingJob
from automation_daemon.speaker_resolution import (
    serialize_job, deserialize_job,
)


def _job() -> RecordingJob:
    return RecordingJob(
        id="m@x", recorded_at="2026-05-15T10:00:00+00:00",
        filename="Subject line", source="plaud-email",
        transcript_data=_td(["Speaker 1", "Alice"]),
        duration_ms=10000,
        source_metadata={
            "plaud_summaries": {"brief": "hi"},
            "infographic_path": Path("99-Attachments/plaud/m.jpg"),
        },
    )


def test_job_roundtrips_through_json() -> None:
    blob = serialize_job(_job())
    assert isinstance(blob, str)
    out = deserialize_job(blob)
    assert out == _job()
    assert isinstance(out.source_metadata["infographic_path"], Path)


def test_roundtrip_without_infographic() -> None:
    j = RecordingJob(
        id="m2", recorded_at="2026-05-15T10:00:00+00:00",
        filename="s", source="plaud-email",
        transcript_data=_td(["Speaker 1"]), duration_ms=None,
        source_metadata={},
    )
    assert deserialize_job(serialize_job(j)) == j
