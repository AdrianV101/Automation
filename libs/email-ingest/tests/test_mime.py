from __future__ import annotations

from email_ingest.mime import parse_email, ParsedEmail


def test_parse_real_plaud_email_returns_headers(real_01: bytes) -> None:
    parsed = parse_email(real_01)
    assert isinstance(parsed, ParsedEmail)
    assert parsed.headers["From"].endswith("<no-reply@plaud.ai>")
    assert parsed.headers["Subject"].startswith("[Plaud-AutoFlow]")
    assert parsed.message_id  # non-empty


def test_parse_real_plaud_email_has_attachments(real_01: bytes) -> None:
    parsed = parse_email(real_01)
    names = [a.filename for a in parsed.attachments]
    assert "transcript.txt" in names
    assert any(n.startswith("summary-") and n.endswith(".txt") for n in names)
    assert any(n.startswith("image_") and n.endswith(".jpg") for n in names)


def test_attachment_bytes_are_decoded(real_01: bytes) -> None:
    parsed = parse_email(real_01)
    transcript = next(a for a in parsed.attachments if a.filename == "transcript.txt")
    assert isinstance(transcript.content, bytes)
    assert len(transcript.content) > 0
    assert b"Speaker" in transcript.content or b"speaker" in transcript.content.lower()


def test_date_header_extracted(real_01: bytes) -> None:
    parsed = parse_email(real_01)
    assert parsed.headers["Date"]


def test_empty_transcript_parses(synth_empty_transcript: bytes) -> None:
    parsed = parse_email(synth_empty_transcript)
    transcript = next(a for a in parsed.attachments if a.filename == "transcript.txt")
    assert transcript.content == b""


def test_missing_transcript_still_parses_email(synth_no_transcript: bytes) -> None:
    # The MIME parser itself is permissive — Plaud-specific validation happens later.
    parsed = parse_email(synth_no_transcript)
    names = [a.filename for a in parsed.attachments]
    assert "transcript.txt" not in names
