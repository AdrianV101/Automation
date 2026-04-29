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


# --- handle_news_email -----------------------------------------------------

from unittest.mock import AsyncMock
from email_ingest import NewsIngestStateDB
from audio_ingest.news_email_adapter import handle_news_email


@pytest.mark.asyncio
async def test_handle_news_email_happy_path(tmp_path):
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    raw = (FIXTURES / "html_newsletter.eml").read_bytes()
    notifier = AsyncMock()

    await handle_news_email(
        uid=1, raw=raw,
        db=db, vault_root=tmp_path,
        telegram_notifier=notifier, news_topic_id=42,
    )

    event = await db.get_event("html-newsletter-001@example-news.com")
    assert event["status"] == "written"
    assert event["vault_note_path"]
    assert (tmp_path / "00-Inbox" / "news" / "2026-04-29").is_dir()
    notifier.assert_awaited_once()
    args, kwargs = notifier.await_args
    assert kwargs.get("thread_id") == 42
    assert "Newsletter HQ" in args[0]
    assert "Weekly Roundup #42" in args[0]


@pytest.mark.asyncio
async def test_handle_news_email_idempotent_skip(tmp_path):
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    raw = (FIXTURES / "html_newsletter.eml").read_bytes()
    notifier = AsyncMock()

    await handle_news_email(
        uid=1, raw=raw,
        db=db, vault_root=tmp_path,
        telegram_notifier=notifier, news_topic_id=None,
    )
    notifier.reset_mock()
    await handle_news_email(
        uid=2, raw=raw,
        db=db, vault_root=tmp_path,
        telegram_notifier=notifier, news_topic_id=None,
    )
    notifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_news_email_malformed_marks_failed(tmp_path):
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    raw = (FIXTURES / "empty_body.eml").read_bytes()
    notifier = AsyncMock()

    await handle_news_email(
        uid=1, raw=raw,
        db=db, vault_root=tmp_path,
        telegram_notifier=notifier, news_topic_id=None,
    )

    event = await db.get_event("empty-001@example.com")
    assert event["status"] == "failed"
    assert "no usable body" in (event["error"] or "")
    notifier.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_news_email_telegram_failure_does_not_revert_state(tmp_path):
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    raw = (FIXTURES / "html_newsletter.eml").read_bytes()
    notifier = AsyncMock(side_effect=RuntimeError("network down"))

    await handle_news_email(
        uid=1, raw=raw,
        db=db, vault_root=tmp_path,
        telegram_notifier=notifier, news_topic_id=None,
    )

    event = await db.get_event("html-newsletter-001@example-news.com")
    # Vault was written successfully; status stays 'written' even though Telegram raised.
    assert event["status"] == "written"


@pytest.mark.asyncio
async def test_handle_news_email_unexpected_exception_marks_failed(tmp_path):
    """Catch-all: any exception from the inner pipeline must mark the row failed.

    Without the outer except, exceptions from yaml/bs4/markdownify would
    leave the row stuck in 'received' and dedup-skipped on subsequent runs.
    """
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    raw = (FIXTURES / "html_newsletter.eml").read_bytes()
    notifier = AsyncMock()

    from unittest.mock import patch
    with patch(
        "audio_ingest.news_email_adapter.render_body",
        side_effect=RuntimeError("simulated bs4 explosion"),
    ):
        await handle_news_email(
            uid=1, raw=raw,
            db=db, vault_root=tmp_path,
            telegram_notifier=notifier, news_topic_id=None,
        )

    event = await db.get_event("html-newsletter-001@example-news.com")
    assert event["status"] == "failed"
    assert "simulated bs4 explosion" in (event["error"] or "")
    notifier.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_news_email_no_message_id_skips_silently(tmp_path):
    """No Message-ID → skip without writing a row or notifying. Common in spam.

    Without this branch, an empty-string PRIMARY KEY would dedupe every
    future no-Message-ID email after the first.
    """
    raw = b"From: x@y.com\r\nSubject: hi\r\n\r\nbody"
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    notifier = AsyncMock()

    await handle_news_email(
        uid=1, raw=raw,
        db=db, vault_root=tmp_path,
        telegram_notifier=notifier, news_topic_id=None,
    )

    notifier.assert_not_awaited()
    assert await db.is_processed("") is False


@pytest.mark.asyncio
async def test_handle_news_email_oserror_on_write_marks_failed(tmp_path):
    """OSError during vault write (disk full, perms) → status='failed'."""
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    raw = (FIXTURES / "html_newsletter.eml").read_bytes()
    notifier = AsyncMock()

    # Make the vault root un-mkdir-able by pointing at a path whose parent
    # is a regular file (not a directory).
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    bad_vault = blocker / "vault"

    await handle_news_email(
        uid=1, raw=raw,
        db=db, vault_root=bad_vault,
        telegram_notifier=notifier, news_topic_id=None,
    )

    event = await db.get_event("html-newsletter-001@example-news.com")
    assert event["status"] == "failed"
    assert event["error"]  # populated
    notifier.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_news_email_post_write_db_failure_alerts(tmp_path):
    """If the post-write update_status fails, the user must be notified —
    otherwise the vault has the file, the DB still says 'received', and
    dedup will skip it forever."""
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    raw = (FIXTURES / "html_newsletter.eml").read_bytes()
    notifier = AsyncMock()

    real_update = db.update_status
    call_count = {"n": 0}

    async def flaky_update(message_id, status, **fields):
        call_count["n"] += 1
        # First call (the 'received' insert is via insert_event, not
        # update_status) — the FIRST update_status call is the 'written'
        # transition. Make it fail.
        if call_count["n"] == 1 and status == "written":
            raise RuntimeError("simulated DB write failure")
        await real_update(message_id, status, **fields)

    db.update_status = flaky_update  # type: ignore[method-assign]

    await handle_news_email(
        uid=1, raw=raw,
        db=db, vault_root=tmp_path,
        telegram_notifier=notifier, news_topic_id=None,
    )

    notifier.assert_awaited_once()
    args, _ = notifier.await_args
    assert "partial" in args[0].lower() or "failed" in args[0].lower()
