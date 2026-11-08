from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation_daemon.plaud_mcp_adapter import (
    is_ready,
    speaker_map,
    strip_plaud_links,
    to_recording_job,
)

FIXTURES = Path(__file__).parent / "fixtures" / "plaud_mcp"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


def test_is_ready_true_false() -> None:
    assert is_ready(_load("file_ready.json")) is True
    assert is_ready(_load("file_not_ready.json")) is False


@pytest.mark.parametrize(
    ("has_transcript", "has_summary", "expected"),
    [(True, False, False), (False, True, False), (True, True, True)],
)
def test_is_ready_requires_both(has_transcript: bool, has_summary: bool, expected: bool) -> None:
    detail = {
        "source_list": [{"data_type": "transaction"}] if has_transcript else [],
        "note_list": [{"data_type": "auto_sum_note"}] if has_summary else [],
    }
    assert is_ready(detail) is expected


def test_speaker_map_first_seen_wins() -> None:
    segs = [
        {"speaker": "Joel", "original_speaker": "Speaker 1"},
        {"speaker": "Joel L", "original_speaker": "Speaker 1"},
    ]
    assert speaker_map(segs) == {"Speaker 1": "Joel"}


def test_strip_plaud_links() -> None:
    md = "a ![](plaud://image?type=x&id=1) b [](plaud://highlight-card?title=X) c **bold** [l](https://x.y)"
    out = strip_plaud_links(md)
    assert "plaud://" not in out
    assert "**bold**" in out
    assert "[l](https://x.y)" in out


def test_job_mapping() -> None:
    segs = _load("segments.json")
    job = to_recording_job(_load("file_ready.json"), segs, [])
    assert job.id == "f_synthetic01"
    assert job.transcript_data.job_id == "f_synthetic01"
    assert job.recorded_at == "2026-11-06T15:32:42.020000+00:00"
    assert job.source == "plaud-mcp"
    assert job.filename == "Synthetic planning sync"
    assert job.duration_ms == 125000
    assert job.transcript_data.duration_seconds == 125.0
    seg0 = job.transcript_data.segments[0]
    assert seg0.start == 1.79
    assert seg0.end == 15.63
    assert job.transcript_data.speakers == ["Joel", "Maya", "Speaker 3"]
    assert job.transcript_data.full_text == "\n\n".join(s["content"] for s in segs)
    summaries = job.source_metadata["plaud_summaries"]
    assert list(summaries) == ["Meeting Summary"]
    assert "plaud://" not in summaries["Meeting Summary"]
    assert "budget" in summaries["Meeting Summary"]
    assert job.source_metadata["plaud_outline"] == "1. Intro\n2. Budget review"
    assert "plaud_highlights" not in job.source_metadata

    other = dict(_load("file_ready.json"), start_at="2026-10-29T17:40:29")
    assert to_recording_job(other, segs, []).recorded_at == "2026-10-29T17:40:29+00:00"


def test_highlight_maps_to_containing_segment() -> None:
    segs = _load("segments.json")
    job = to_recording_job(_load("file_ready.json"), segs, [{"start_time": 16500}])
    assert job.source_metadata["plaud_highlights"] == [
        {"time_s": 16.5, "text": "Let us look at the budget first."}
    ]


def test_highlight_unknown_shape_raises() -> None:
    with pytest.raises(ValueError, match="foo"):
        to_recording_job(
            _load("file_ready.json"), _load("segments.json"), [{"foo": "bar"}]
        )


def test_highlight_partial_unusable_keeps_usable(caplog) -> None:
    with caplog.at_level("WARNING"):
        job = to_recording_job(
            _load("file_ready.json"),
            _load("segments.json"),
            [{"foo": "bar"}, {"start_time": 16500}],
        )
    assert len(job.source_metadata["plaud_highlights"]) == 1
    assert "foo" in caplog.text


def test_highlight_outside_segments_uses_nearest() -> None:
    segs = _load("segments.json")
    job = to_recording_job(_load("file_ready.json"), segs, [{"start_time": 10_000_000}])
    assert job.source_metadata["plaud_highlights"][0]["text"] == segs[-1]["content"]


def test_start_at_with_offset_converted_to_utc() -> None:
    detail = dict(_load("file_ready.json"), start_at="2026-11-06T17:32:42+02:00")
    job = to_recording_job(detail, _load("segments.json"), [])
    assert job.recorded_at == "2026-11-06T15:32:42+00:00"


def test_duration_none() -> None:
    detail = dict(_load("file_ready.json"), duration=None)
    job = to_recording_job(detail, _load("segments.json"), [])
    assert job.duration_ms is None
    assert job.transcript_data.duration_seconds is None


def test_duplicate_summary_names_both_kept() -> None:
    detail = _load("file_ready.json")
    note = dict(detail["note_list"][0], data_content="Second body")
    detail = dict(detail, note_list=[detail["note_list"][0], note])
    summaries = to_recording_job(detail, _load("segments.json"), []).source_metadata[
        "plaud_summaries"
    ]
    assert len(summaries) == 2
    assert "Second body" in summaries.values()


def test_missing_speaker_label_tolerated() -> None:
    segs = [dict(s) for s in _load("segments.json")]
    del segs[0]["speaker"]
    job = to_recording_job(_load("file_ready.json"), segs, [])
    assert "Unknown" in job.transcript_data.speakers
