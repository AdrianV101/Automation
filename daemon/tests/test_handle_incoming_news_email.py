"""End-to-end news capture: raw bytes → vault note + state row + Telegram call."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from audio_ingest.news_email_adapter import handle_news_email
from email_ingest import NewsIngestStateDB

FIXTURES = Path(__file__).parent / "fixtures" / "news"


@pytest.mark.asyncio
async def test_full_capture_loop_html_newsletter(tmp_path):
    db = NewsIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    notifier = AsyncMock()

    raw = (FIXTURES / "html_newsletter.eml").read_bytes()
    await handle_news_email(
        uid=1, raw=raw, headers={},
        db=db, vault_root=tmp_path,
        telegram_notifier=notifier, news_topic_id=42,
    )

    # State row
    event = await db.get_event("html-newsletter-001@example-news.com")
    assert event is not None
    assert event["status"] == "written"
    assert event["sender"] is not None
    rel_path = event["vault_note_path"]
    assert rel_path.startswith("00-Inbox/news/2026-04-29/")

    # Vault file exists with valid frontmatter
    note_path = tmp_path / rel_path
    assert note_path.is_file()
    text = note_path.read_text()
    fm_end = text.index("\n---\n", 4)  # opening --- is at byte 0
    fm = yaml.safe_load(text[4:fm_end])
    assert fm["type"] == "news-item"
    assert fm["source"] == "Newsletter HQ"
    assert fm["source-address"] == "weekly@example-news.com"
    assert fm["message-id"] == "html-newsletter-001@example-news.com"
    assert "news" in fm["tags"]

    # Body looks like markdown not HTML
    body = text[fm_end + len("\n---\n"):].strip()
    assert "<html>" not in body
    assert "Weekly Roundup #42" in body

    # Telegram called once with the right topic
    notifier.assert_awaited_once()
    args, kwargs = notifier.await_args
    assert kwargs["thread_id"] == 42
    assert "📰" in args[0]
