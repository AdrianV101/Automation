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


# --- write_news_note -------------------------------------------------------

import yaml
from audio_ingest.news_email_adapter import write_news_note


def test_write_news_note_writes_to_date_folder(tmp_path):
    payload = email_to_news_note(_load("html_newsletter.eml"))
    body_md = render_body(_load("html_newsletter.eml"))
    path = write_news_note(payload, body_md=body_md, vault_root=tmp_path)
    assert path.is_file()
    assert path.parent == tmp_path / "00-Inbox" / "news" / "2026-04-29"
    assert path.name.startswith("weekly-roundup-42-")
    assert path.suffix == ".md"


def test_write_news_note_frontmatter_shape(tmp_path):
    payload = email_to_news_note(_load("html_newsletter.eml"))
    body_md = render_body(_load("html_newsletter.eml"))
    path = write_news_note(payload, body_md=body_md, vault_root=tmp_path)
    content = path.read_text()
    assert content.startswith("---\n")
    fm_end = content.index("\n---\n", 4)
    fm = yaml.safe_load(content[4:fm_end])
    assert fm["type"] == "news-item"
    assert fm["source-type"] == "newsletter"
    assert fm["source"] == "Newsletter HQ"
    assert fm["source-address"] == "weekly@example-news.com"
    assert fm["message-id"] == "html-newsletter-001@example-news.com"
    assert fm["received-at"] == "2026-04-29T08:14:32+00:00"
    assert fm["created"] == "2026-04-29"
    assert fm["tags"] == ["news", "source-newsletter"]
    body = content[fm_end + len("\n---\n"):].strip()
    assert body == body_md.rstrip()


def test_write_news_note_idempotent(tmp_path):
    payload = email_to_news_note(_load("html_newsletter.eml"))
    body_md = render_body(_load("html_newsletter.eml"))
    p1 = write_news_note(payload, body_md=body_md, vault_root=tmp_path)
    p2 = write_news_note(payload, body_md=body_md, vault_root=tmp_path)
    assert p1 == p2
    assert p1.read_text() == p2.read_text()


def test_write_news_note_creates_date_folder(tmp_path):
    payload = email_to_news_note(_load("plaintext_newsletter.eml"))
    body_md = render_body(_load("plaintext_newsletter.eml"))
    path = write_news_note(payload, body_md=body_md, vault_root=tmp_path)
    assert path.parent.is_dir()
    assert path.parent.name == "2026-04-29"
