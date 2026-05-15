from __future__ import annotations

from pathlib import Path

import pytest

from email_ingest import parse_email

from automation_daemon.plaud_email_adapter import (
    Infographic,
    PlaudEmailPayload,
    MalformedPlaudEmailError,
    parse_plaud_email,
)

FIXTURES = Path(__file__).parent / "fixtures" / "emails"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_parse_real_plaud_email() -> None:
    parsed = parse_email(_load("plaud_real_01.eml"))
    payload = parse_plaud_email(parsed)
    assert isinstance(payload, PlaudEmailPayload)
    assert payload.transcript_text
    assert len(payload.summaries) >= 1
    assert payload.infographic is not None


def test_wrong_from_returns_none() -> None:
    parsed = parse_email(_load("synth_wrong_from.eml"))
    assert parse_plaud_email(parsed) is None


def test_wrong_subject_returns_none() -> None:
    parsed = parse_email(_load("plaud_real_01.eml"))
    # ParsedEmail is frozen but headers dict is still mutable.
    parsed.headers["Subject"] = "[Unrelated] stuff"
    assert parse_plaud_email(parsed) is None


def test_missing_transcript_raises() -> None:
    parsed = parse_email(_load("synth_no_transcript.eml"))
    with pytest.raises(MalformedPlaudEmailError):
        parse_plaud_email(parsed)


def test_single_summary_accepted() -> None:
    parsed = parse_email(_load("synth_single_summary.eml"))
    payload = parse_plaud_email(parsed)
    assert payload is not None
    assert len(payload.summaries) == 1


def test_four_summaries_accepted() -> None:
    parsed = parse_email(_load("synth_four_summaries.eml"))
    payload = parse_plaud_email(parsed)
    assert payload is not None
    assert len(payload.summaries) == 4


def test_missing_image_is_not_fatal() -> None:
    parsed = parse_email(_load("synth_no_image.eml"))
    payload = parse_plaud_email(parsed)
    assert payload is not None
    assert payload.infographic is None


from pkm import TranscriptData

from automation_daemon.plaud_email_adapter import plaud_email_to_transcript_data, PlaudEmailPayload


def _payload(transcript_text: str) -> PlaudEmailPayload:
    return PlaudEmailPayload(
        message_id="<m@x>",
        date_header="Mon, 21 Apr 2025 12:00:00 +0000",
        subject="[Plaud-AutoFlow] hi",
        transcript_text=transcript_text,
    )


TRANSCRIPT_SAMPLE = """\
Speaker 1 00:00:00
Hello there.

Speaker 2 00:00:05
General Kenobi.

Speaker 1 00:00:10
You are a bold one.
"""


def test_parse_speaker_turns_basic() -> None:
    td = plaud_email_to_transcript_data(
        _payload(TRANSCRIPT_SAMPLE), job_id="job-1", recorded_at="2025-04-21T12:00:00+00:00",
    )
    assert isinstance(td, TranscriptData)
    assert len(td.segments) == 3
    assert td.segments[0].speaker == "Speaker 1"
    assert td.segments[0].text == "Hello there."
    assert td.segments[0].start == 0.0
    assert td.segments[0].end == 5.0
    assert td.segments[1].start == 5.0
    assert td.segments[1].end == 10.0
    assert td.segments[2].start == 10.0
    assert td.segments[2].end == 10.0  # last turn — end == start
    assert set(td.speakers) == {"Speaker 1", "Speaker 2"}
    assert td.duration_seconds == 10.0


def test_parse_speaker_turns_named_speaker() -> None:
    text = "Speaker_1 00:00:00\nHi.\n\nSpeaker_1 00:00:03\nMore.\n"
    td = plaud_email_to_transcript_data(
        _payload(text), job_id="j", recorded_at="2025-04-21T12:00:00+00:00",
    )
    assert td.segments[0].speaker == "Speaker_1"
    assert td.speakers == ["Speaker_1"]


def test_parse_empty_transcript_falls_back() -> None:
    td = plaud_email_to_transcript_data(
        _payload(""), job_id="j", recorded_at="2025-04-21T12:00:00+00:00",
    )
    assert len(td.segments) == 1
    assert td.segments[0].speaker == "Speaker 1"
    assert td.segments[0].text == ""
    assert td.duration_seconds == 0.0


def test_parse_malformed_no_timestamps_falls_back() -> None:
    td = plaud_email_to_transcript_data(
        _payload("just some text with no structure"),
        job_id="j", recorded_at="2025-04-21T12:00:00+00:00",
    )
    assert len(td.segments) == 1
    assert td.segments[0].text == "just some text with no structure"


from automation_daemon.plaud_email_adapter import (
    SavedArtifacts,
    save_plaud_attachments,
    rewrite_plaud_summary_links,
)


def test_save_writes_jpeg(tmp_path: Path) -> None:
    payload = PlaudEmailPayload(
        message_id="<abc123@x>",
        date_header="",
        subject="[Plaud-AutoFlow] hi",
        transcript_text="",
        infographic=Infographic(content=b"jpegbytes", filename="image_hash.jpg"),
    )
    artifacts = save_plaud_attachments(
        payload, vault_path=tmp_path, attachments_subdir="99-Attachments/plaud",
    )
    assert isinstance(artifacts, SavedArtifacts)
    assert artifacts.infographic_path is not None
    saved = tmp_path / "99-Attachments" / "plaud" / "abc123@x.jpg"
    assert saved.exists()
    assert saved.read_bytes() == b"jpegbytes"


def test_save_is_idempotent(tmp_path: Path) -> None:
    payload = PlaudEmailPayload(
        message_id="<abc123@x>", date_header="", subject="[Plaud-AutoFlow]",
        transcript_text="",
        infographic=Infographic(content=b"first", filename="image_hash.jpg"),
    )
    save_plaud_attachments(payload, vault_path=tmp_path, attachments_subdir="99-Attachments/plaud")
    payload2 = PlaudEmailPayload(
        message_id="<abc123@x>", date_header="", subject="[Plaud-AutoFlow]",
        transcript_text="",
        infographic=Infographic(content=b"second", filename="image_hash.jpg"),
    )
    save_plaud_attachments(payload2, vault_path=tmp_path, attachments_subdir="99-Attachments/plaud")
    saved = tmp_path / "99-Attachments" / "plaud" / "abc123@x.jpg"
    assert saved.read_bytes() == b"first"


def test_save_handles_no_image(tmp_path: Path) -> None:
    payload = PlaudEmailPayload(
        message_id="<a@x>", date_header="", subject="[Plaud-AutoFlow]",
        transcript_text="",
    )
    artifacts = save_plaud_attachments(payload, vault_path=tmp_path, attachments_subdir="99-Attachments/plaud")
    assert artifacts.infographic_path is None


def test_rewrite_summary_links_replaces_plaud_note_reference() -> None:
    summaries = {
        "Reasoning Summary": (
            "![PLAUD NOTE](permanent/abc/summary_poster/card_1.png)\n\n"
            "Some summary text."
        ),
        "Voice Note": "No image reference here.",
    }
    rewritten = rewrite_plaud_summary_links(
        summaries, infographic_vault_path=Path("99-Attachments/plaud/abc@x.jpg"),
    )
    assert "![[99-Attachments/plaud/abc@x.jpg]]" in rewritten["Reasoning Summary"]
    assert "permanent/abc" not in rewritten["Reasoning Summary"]
    assert rewritten["Voice Note"] == "No image reference here."


def test_rewrite_summary_links_noop_when_no_infographic() -> None:
    summaries = {"x": "![PLAUD NOTE](permanent/a/summary_poster/card.png)"}
    rewritten = rewrite_plaud_summary_links(summaries, infographic_vault_path=None)
    assert "permanent/a/summary_poster/card.png" in rewritten["x"]


from automation_daemon.plaud_email_adapter import recording_job_from_email
from automation_daemon.models import RecordingJob


def test_recording_job_from_email_wires_everything(tmp_path: Path) -> None:
    raw = (FIXTURES / "plaud_real_01.eml").read_bytes()
    parsed = parse_email(raw)
    job = recording_job_from_email(
        parsed, vault_path=tmp_path, attachments_subdir="99-Attachments/plaud",
    )
    assert isinstance(job, RecordingJob)
    assert job.source == "plaud-email"
    assert job.transcript_data is not None
    assert job.source_metadata.get("plaud_summaries")
    assert job.source_metadata.get("infographic_path") is not None
    assert job.id == parsed.message_id


def test_recording_job_from_email_returns_none_for_non_plaud_mail(tmp_path: Path) -> None:
    parsed = parse_email((FIXTURES / "synth_wrong_from.eml").read_bytes())
    assert recording_job_from_email(
        parsed, vault_path=tmp_path, attachments_subdir="99-Attachments/plaud",
    ) is None
