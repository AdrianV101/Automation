from __future__ import annotations

from audio_ingest.news_email_adapter import slugify_subject


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
