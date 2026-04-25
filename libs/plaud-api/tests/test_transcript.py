from __future__ import annotations

import json
from pathlib import Path

from plaud_api.transcript import Transcript, TranscriptSegment, plaud_segments_to_transcript

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture() -> list[dict]:
    return json.loads((FIXTURES / "plaud_transcript_response.json").read_text())


class TestPlaudSegmentsToTranscript:
    def test_job_id_and_recorded_at(self):
        segments = load_fixture()
        t = plaud_segments_to_transcript(
            segments, job_id="abc-123", recorded_at="2026-02-20T14:00:00Z", duration_ms=250000
        )
        assert t.job_id == "abc-123"
        assert t.recorded_at == "2026-02-20T14:00:00Z"

    def test_duration_converted_to_seconds(self):
        segments = load_fixture()
        t = plaud_segments_to_transcript(
            segments, job_id="j1", recorded_at="2026-01-01T00:00:00Z", duration_ms=250000
        )
        assert t.duration_seconds == 250.0

    def test_segments_converted(self):
        segments = load_fixture()
        t = plaud_segments_to_transcript(
            segments, job_id="j1", recorded_at="2026-01-01T00:00:00Z", duration_ms=None
        )
        assert len(t.segments) == 7

        first = t.segments[0]
        assert isinstance(first, TranscriptSegment)
        assert first.text == segments[0]["content"]
        assert first.start == 6140 / 1000
        assert first.end == 39660 / 1000
        assert first.speaker == "Speaker 1"

        last = t.segments[-1]
        assert last.start == 206614 / 1000
        assert last.end == 242208 / 1000

    def test_speaker_uses_labeled_name(self):
        segments = [
            {
                "content": "Hello",
                "start_time": 0,
                "end_time": 1000,
                "speaker": "Carol",
                "original_speaker": "Speaker 2",
                "embeddingKey": None,
            }
        ]
        t = plaud_segments_to_transcript(
            segments, job_id="j1", recorded_at="2026-01-01T00:00:00Z", duration_ms=5000
        )
        assert t.segments[0].speaker == "Carol"

    def test_unique_sorted_speakers(self):
        segments = [
            {"content": "Hi", "start_time": 0, "end_time": 1000, "speaker": "Zara", "original_speaker": "Speaker 2", "embeddingKey": None},
            {"content": "Hey", "start_time": 1000, "end_time": 2000, "speaker": "Alice", "original_speaker": "Speaker 1", "embeddingKey": None},
            {"content": "Yo", "start_time": 2000, "end_time": 3000, "speaker": "Zara", "original_speaker": "Speaker 2", "embeddingKey": None},
        ]
        t = plaud_segments_to_transcript(
            segments, job_id="j1", recorded_at="2026-01-01T00:00:00Z", duration_ms=3000
        )
        assert t.speakers == ["Alice", "Zara"]

    def test_full_text_format(self):
        segments = [
            {"content": "Hello there", "start_time": 0, "end_time": 1000, "speaker": "Alice", "original_speaker": "Speaker 1", "embeddingKey": None},
            {"content": "Hi Alice", "start_time": 1000, "end_time": 2000, "speaker": "Carol", "original_speaker": "Speaker 2", "embeddingKey": None},
        ]
        t = plaud_segments_to_transcript(
            segments, job_id="j1", recorded_at="2026-01-01T00:00:00Z", duration_ms=2000
        )
        assert t.full_text == "[Alice] Hello there\n[Carol] Hi Alice"

    def test_empty_segments(self):
        t = plaud_segments_to_transcript(
            [], job_id="empty", recorded_at="2026-01-01T00:00:00Z", duration_ms=0
        )
        assert isinstance(t, Transcript)
        assert t.segments == []
        assert t.speakers == []
        assert t.full_text == ""
        assert t.job_id == "empty"

    def test_none_duration(self):
        t = plaud_segments_to_transcript(
            [], job_id="j1", recorded_at="2026-01-01T00:00:00Z", duration_ms=None
        )
        assert t.duration_seconds is None

    def test_fixture_full_integration(self):
        """Full integration test using the real Plaud fixture data."""
        segments = load_fixture()
        t = plaud_segments_to_transcript(
            segments,
            job_id="plaud-rec-001",
            recorded_at="2026-02-20T14:30:00Z",
            duration_ms=242208,
        )
        assert t.job_id == "plaud-rec-001"
        assert t.recorded_at == "2026-02-20T14:30:00Z"
        assert t.duration_seconds == 242.208
        assert t.speakers == ["Speaker 1"]
        assert len(t.segments) == 7
        assert all(isinstance(s, TranscriptSegment) for s in t.segments)
        assert all(s.speaker == "Speaker 1" for s in t.segments)
        assert t.full_text.startswith("[Speaker 1] Basically,")
        assert t.full_text.count("\n") == 6
