from __future__ import annotations

from pathlib import Path

import pytest

from email_ingest import MalformedEmailError, parse_email
from audio_ingest.news_email_adapter import render_body, slugify_subject

FIXTURES = Path(__file__).parent / "fixtures" / "news"


def _load(name: str):
    return parse_email((FIXTURES / name).read_bytes())


def test_slugify_simple():
    s = slugify_subject("Weekly Roundup #42", message_id="abc@x.com")
    parts = s.rsplit("-", 1)
    assert parts[0] == "weekly-roundup-42"
    assert len(parts[1]) == 6  # short hash


def test_slugify_long_subject_truncates_to_60_plus_hash():
    long = "a" * 200
    s = slugify_subject(long, message_id="abc@x.com")
    base, suffix = s.rsplit("-", 1)
    assert len(base) == 60
    assert len(suffix) == 6


def test_slugify_empty_subject():
    s = slugify_subject("", message_id="abc@x.com")
    assert s.startswith("untitled-")


def test_slugify_unicode_normalised():
    s = slugify_subject("Café Update", message_id="abc@x.com")
    assert s.startswith("cafe-update-")


def test_slugify_deterministic():
    s1 = slugify_subject("Hello", message_id="msg@x.com")
    s2 = slugify_subject("Hello", message_id="msg@x.com")
    assert s1 == s2


def test_slugify_different_message_id_changes_hash():
    s1 = slugify_subject("Hello", message_id="msg-a@x.com")
    s2 = slugify_subject("Hello", message_id="msg-b@x.com")
    assert s1 != s2


# --- render_body -----------------------------------------------------------


def test_render_body_html_preserves_structure():
    md = render_body(_load("html_newsletter.eml"))
    assert "Weekly Roundup #42" in md
    assert "[AI infrastructure trends](https://example.com/post1)" in md
    assert "Item one" in md
    assert "Item two" in md


def test_render_body_plaintext_fallback_when_no_html():
    md = render_body(_load("plaintext_newsletter.eml"))
    assert "Plaintext Daily" in md
    assert "Headline one" in md


def test_render_body_strips_tracking_pixel():
    md = render_body(_load("multipart_with_tracking.eml"))
    assert "track.example" not in md


def test_render_body_strips_view_in_browser_link():
    md = render_body(_load("multipart_with_tracking.eml"))
    first_three_lines = "\n".join(md.lstrip().splitlines()[:3])
    assert "View this email in your browser" not in first_three_lines


def test_render_body_drops_style_block():
    md = render_body(_load("multipart_with_tracking.eml"))
    assert ".foo {" not in md
    assert "color: red" not in md


def test_render_body_no_body_raises_malformed():
    with pytest.raises(MalformedEmailError):
        render_body(_load("empty_body.eml"))


# --- email_to_news_note ----------------------------------------------------

from datetime import datetime, timezone
from audio_ingest.news_email_adapter import NewsNotePayload, email_to_news_note


def test_email_to_news_note_extracts_sender_display():
    payload = email_to_news_note(_load("html_newsletter.eml"))
    assert isinstance(payload, NewsNotePayload)
    assert payload.sender_display == "Newsletter HQ"
    assert payload.sender_address == "weekly@example-news.com"
    assert payload.subject == "Weekly Roundup #42 — AI infrastructure"
    assert payload.message_id == "html-newsletter-001@example-news.com"
    assert payload.received_at == datetime(2026, 4, 29, 8, 14, 32, tzinfo=timezone.utc)


def test_email_to_news_note_falls_back_to_domain_when_no_display_name():
    payload = email_to_news_note(_load("plaintext_newsletter.eml"))
    assert payload.sender_display == "plaintext-news.org"
    assert payload.sender_address == "hello@plaintext-news.org"


def test_email_to_news_note_no_subject_falls_back_to_empty():
    payload = email_to_news_note(_load("no_subject.eml"))
    assert payload.subject == ""
