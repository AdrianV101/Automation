from pkm import TranscriptData

from audio_ingest.models import RecordingJob


def _transcript(job_id: str = "test123") -> TranscriptData:
    return TranscriptData(
        job_id=job_id,
        recorded_at="2026-02-06T12:00:00+00:00",
        duration_seconds=60.0,
        speakers=["A"],
        segments=[],
        full_text="Hello",
    )


class TestRecordingJob:
    def test_create_minimal(self):
        transcript = _transcript()
        job = RecordingJob(
            id="test123",
            recorded_at="2026-02-06T12:00:00+00:00",
            filename="Test Recording",
            source="plaud-email",
            transcript_data=transcript,
        )
        assert job.id == "test123"
        assert job.source == "plaud-email"
        assert job.transcript_data is transcript
        assert job.duration_ms is None
        assert job.source_metadata == {}

    def test_create_with_source_metadata(self):
        transcript = _transcript()
        job = RecordingJob(
            id="test123",
            recorded_at="2026-02-06T12:00:00+00:00",
            filename="Test Recording",
            source="plaud-email",
            transcript_data=transcript,
            duration_ms=60000,
            source_metadata={"plaud_summaries": {"Notes": "hi"}},
        )
        assert job.duration_ms == 60000
        assert job.source_metadata["plaud_summaries"] == {"Notes": "hi"}

    def test_create_with_custom_source(self):
        job = RecordingJob(
            id="cal-001",
            recorded_at="2026-02-06T12:00:00+00:00",
            filename="Team Standup",
            source="calendar",
            transcript_data=_transcript(),
        )
        assert job.source == "calendar"
