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
