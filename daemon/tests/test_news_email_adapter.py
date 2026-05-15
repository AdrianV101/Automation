from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from email_ingest import MalformedEmailError, NewsIngestStateDB, parse_email
from news_pipeline import NewsItem
from automation_daemon.news_email_adapter import (
    _classify_source_type, email_to_news_item, handle_news_email, render_body,
)

FIXTURES = Path(__file__).parent / "fixtures" / "news"


def _load(name: str):
    return parse_email((FIXTURES / name).read_bytes())


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


def test_render_body_handles_substack_nested_tracking_pixels():
    """Nested <img> from html.parser's void-element handling must not crash the pixel filter."""
    md = render_body(_load("substack_nested_img.eml"))
    assert "eotrx.substackcdn.com" not in md
    assert "email.mg-d0.substack.com" not in md


def test_render_body_no_body_raises_malformed():
    with pytest.raises(MalformedEmailError):
        render_body(_load("empty_body.eml"))


# --- email_to_news_item ----------------------------------------------------


def test_email_to_news_item_extracts_sender_display():
    parsed = _load("html_newsletter.eml")
    item = email_to_news_item(parsed, body_md="ignored")
    assert isinstance(item, NewsItem)
    assert item.source == "Newsletter HQ"
    assert item.source_address == "weekly@example-news.com"
    assert item.source_type == "newsletter"
    assert item.subject == "Weekly Roundup #42 — AI infrastructure"
    assert item.message_id == "html-newsletter-001@example-news.com"
    assert item.received_at == datetime(2026, 4, 29, 8, 14, 32, tzinfo=timezone.utc)


def test_email_to_news_item_falls_back_to_domain_when_no_display_name():
    item = email_to_news_item(_load("plaintext_newsletter.eml"), body_md="x")
    assert item.source == "plaintext-news.org"
    assert item.source_address == "hello@plaintext-news.org"


def test_email_to_news_item_no_subject_falls_back_to_empty():
    item = email_to_news_item(_load("no_subject.eml"), body_md="x")
    assert item.subject == ""


# --- _classify_source_type -------------------------------------------------


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("digest@ft.com", "financial-times"),
        ("newsletters@email.ft.com", "financial-times"),
        ("Mixed.Case@FT.COM", "financial-times"),
        ("weekly@example-news.com", "newsletter"),
        ("hello@plaintext-news.org", "newsletter"),
        ("", "newsletter"),
        ("not-an-email", "newsletter"),
        # Right-anchored match: ft.com must be the registered domain, not a
        # substring of the host. ft.com.attacker.example is NOT FT.
        ("phish@ft.com.attacker.example", "newsletter"),
    ],
)
def test_classify_source_type(address, expected):
    assert _classify_source_type(address) == expected


def test_email_to_news_item_ft_email_classified_as_financial_times():
    item = email_to_news_item(_load("firstft_email.eml"), body_md="x")
    assert item.source_type == "financial-times"
    assert item.source == "Financial Times"
    assert item.source_address == "newsletters@email.ft.com"


# --- end-to-end via shared writer ------------------------------------------


def test_newsletter_write_lands_in_dated_folder(tmp_path):
    from news_pipeline import write_news_item
    parsed = _load("html_newsletter.eml")
    body_md = render_body(parsed)
    item = email_to_news_item(parsed, body_md)
    path = write_news_item(item, tmp_path)
    assert path.is_file()
    assert path.parent == tmp_path / "00-Inbox" / "news" / "2026-04-29"
    assert path.name.startswith("weekly-roundup-42-")


def test_newsletter_frontmatter_round_trip(tmp_path):
    """Schema preserved exactly — every key the master-doc generator reads."""
    from news_pipeline import write_news_item
    parsed = _load("html_newsletter.eml")
    body_md = render_body(parsed)
    item = email_to_news_item(parsed, body_md)
    path = write_news_item(item, tmp_path)
    content = path.read_text()
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


# --- handle_news_email -----------------------------------------------------


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
    assert event["status"] == "written"


@pytest.mark.asyncio
async def test_handle_news_email_unexpected_exception_marks_failed(tmp_path):
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    raw = (FIXTURES / "html_newsletter.eml").read_bytes()
    notifier = AsyncMock()

    with patch(
        "automation_daemon.news_email_adapter.render_body",
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
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    raw = (FIXTURES / "html_newsletter.eml").read_bytes()
    notifier = AsyncMock()

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
    assert event["error"]
    notifier.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_news_email_post_write_db_failure_alerts(tmp_path):
    """Vault note is written but DB update_status fails — alert the operator."""
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    raw = (FIXTURES / "html_newsletter.eml").read_bytes()
    notifier = AsyncMock()

    real_update = db.update_status
    call_count = {"n": 0}

    async def flaky_update(message_id, status, **fields):
        call_count["n"] += 1
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
