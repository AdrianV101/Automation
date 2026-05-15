"""Tests for news_daily_master.prompt."""
from __future__ import annotations

from datetime import date

from automation_daemon.news_daily_master.prompt import build_runner_prompt


class TestBuildRunnerPrompt:
    def test_includes_target_date_iso(self) -> None:
        out = build_runner_prompt(date(2026, 4, 29))
        assert "2026-04-29" in out

    def test_invokes_skill_by_name(self) -> None:
        out = build_runner_prompt(date(2026, 4, 29))
        assert "news-daily-master" in out

    def test_mentions_source_folder(self) -> None:
        out = build_runner_prompt(date(2026, 4, 29))
        assert "00-Inbox/news/2026-04-29" in out

    def test_mentions_master_doc_target(self) -> None:
        out = build_runner_prompt(date(2026, 4, 29))
        assert "01-Projects/News/daily/2026-04-29-master.md" in out
