from __future__ import annotations

from automation_daemon.hacker_news_adapter import (
    PollSummary, build_telegram_summary,
)


def test_summary_message_with_ingested_items():
    s = PollSummary(
        ingested=23, skipped_dedupe=2, fetch_failures=0, write_failures=0,
        db_failures=0, top_title="A new way to do X", top_points=1284,
    )
    msg = build_telegram_summary(s, date_folder="2026-05-01")
    assert "📰 Hacker News" in msg
    assert "23 stories ingested" in msg
    assert "A new way to do X" in msg
    assert "1284p" in msg
    assert "00-Inbox/news/2026-05-01" in msg


def test_summary_message_zero_ingested_zero_dedupe():
    s = PollSummary(
        ingested=0, skipped_dedupe=0, fetch_failures=0, write_failures=0,
        db_failures=0, top_title="", top_points=0,
    )
    msg = build_telegram_summary(s, date_folder="2026-05-01")
    assert "0 stories ingested" in msg
    # No "top:" line when there is no top story:
    assert "top:" not in msg.lower()


def test_summary_message_includes_failures_when_present():
    s = PollSummary(
        ingested=5, skipped_dedupe=0, fetch_failures=3, write_failures=1,
        db_failures=0, top_title="t", top_points=200,
    )
    msg = build_telegram_summary(s, date_folder="2026-05-01")
    assert "3 fetch failures" in msg or "3 fetch failure" in msg
    assert "1 write failure" in msg


def test_summary_message_includes_db_failures_when_present():
    s = PollSummary(
        ingested=4, skipped_dedupe=0, fetch_failures=0, write_failures=0,
        db_failures=2, top_title="t", top_points=200,
    )
    msg = build_telegram_summary(s, date_folder="2026-05-01")
    assert "2 db failures" in msg
