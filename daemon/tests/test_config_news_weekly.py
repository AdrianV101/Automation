from __future__ import annotations

import pytest

from automation_daemon.config import DaemonConfig


def test_weekly_defaults_present() -> None:
    cfg = DaemonConfig()
    assert cfg.news_weekly_enabled is False
    assert cfg.news_weekly_model == "claude-sonnet-4-6"
    assert cfg.news_weekly_fire_weekday == 6  # Sunday, date.weekday() convention
    assert cfg.news_weekly_fire_time == "18:00"
    assert cfg.news_weekly_backfill_weeks == 2
    assert cfg.news_weekly_min_days == 3
    assert cfg.news_weekly_recurrence_threshold == 3
    assert cfg.news_weekly_max_threads == 8
    assert cfg.news_weekly_max_turns == 60


def test_weekly_from_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("PKM_VAULT_PATH", "/tmp/vault")
    monkeypatch.setenv("NEWS_WEEKLY_ENABLED", "true")
    monkeypatch.setenv("NEWS_WEEKLY_MODEL", "claude-opus-4-7")
    monkeypatch.setenv("NEWS_WEEKLY_FIRE_WEEKDAY", "5")
    monkeypatch.setenv("NEWS_WEEKLY_FIRE_TIME", "20:30")
    monkeypatch.setenv("NEWS_WEEKLY_BACKFILL_WEEKS", "4")
    monkeypatch.setenv("NEWS_WEEKLY_MIN_DAYS", "5")
    monkeypatch.setenv("NEWS_WEEKLY_RECURRENCE_THRESHOLD", "4")
    monkeypatch.setenv("NEWS_WEEKLY_MAX_THREADS", "10")
    monkeypatch.setenv("NEWS_WEEKLY_MAX_TURNS", "90")
    cfg = DaemonConfig.from_env()
    assert cfg.news_weekly_enabled is True
    assert cfg.news_weekly_model == "claude-opus-4-7"
    assert cfg.news_weekly_fire_weekday == 5
    assert cfg.news_weekly_fire_time == "20:30"
    assert cfg.news_weekly_backfill_weeks == 4
    assert cfg.news_weekly_min_days == 5
    assert cfg.news_weekly_recurrence_threshold == 4
    assert cfg.news_weekly_max_threads == 10
    assert cfg.news_weekly_max_turns == 90


def test_weekly_invalid_fire_time_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("PKM_VAULT_PATH", "/tmp/vault")
    monkeypatch.setenv("NEWS_WEEKLY_ENABLED", "true")
    monkeypatch.setenv("NEWS_WEEKLY_FIRE_TIME", "notatime")
    with pytest.raises(ValueError, match="NEWS_WEEKLY_FIRE_TIME"):
        DaemonConfig.from_env()
