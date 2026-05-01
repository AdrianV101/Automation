import pytest

from audio_ingest.config import DaemonConfig


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setenv("PKM_VAULT_PATH", "/tmp/vault")


def test_hacker_news_defaults_disabled(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.delenv("HACKER_NEWS_ENABLED", raising=False)
    cfg = DaemonConfig.from_env()
    assert cfg.hacker_news_enabled is False
    assert cfg.hacker_news_local_time == "05:30"
    assert cfg.hacker_news_min_points == 100
    assert cfg.hacker_news_max_items == 25
    assert cfg.hacker_news_topstories_pool == 50


def test_hacker_news_enabled_loads(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("HACKER_NEWS_ENABLED", "true")
    monkeypatch.setenv("HACKER_NEWS_LOCAL_TIME", "07:15")
    monkeypatch.setenv("HACKER_NEWS_MIN_POINTS", "150")
    monkeypatch.setenv("HACKER_NEWS_MAX_ITEMS", "30")
    monkeypatch.setenv("HACKER_NEWS_TOPSTORIES_POOL", "75")
    cfg = DaemonConfig.from_env()
    assert cfg.hacker_news_enabled is True
    assert cfg.hacker_news_local_time == "07:15"
    assert cfg.hacker_news_min_points == 150
    assert cfg.hacker_news_max_items == 30
    assert cfg.hacker_news_topstories_pool == 75


def test_hacker_news_invalid_local_time_raises(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("HACKER_NEWS_ENABLED", "true")
    monkeypatch.setenv("HACKER_NEWS_LOCAL_TIME", "25:99")
    with pytest.raises(ValueError, match="HACKER_NEWS_LOCAL_TIME"):
        DaemonConfig.from_env()
