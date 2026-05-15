import pytest

from audio_ingest.config import DaemonConfig

_BASE = {
    "TELEGRAM_BOT_TOKEN": "tok",
    "TELEGRAM_CHAT_ID": "chat",
    "PKM_VAULT_PATH": "/tmp/vault",
}


def _set_base(monkeypatch):
    for k, v in _BASE.items():
        monkeypatch.setenv(k, v)


def test_news_research_defaults(monkeypatch):
    _set_base(monkeypatch)
    monkeypatch.setenv("NEWS_RESEARCH_ENABLED", "false")
    cfg = DaemonConfig.from_env()
    assert cfg.news_research_enabled is False
    assert cfg.news_research_model == "claude-sonnet-4-6"
    assert cfg.news_research_max_items == 3
    assert cfg.news_research_max_turns == 60


def test_news_research_loads_from_env(monkeypatch):
    _set_base(monkeypatch)
    monkeypatch.setenv("NEWS_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("NEWS_RESEARCH_MODEL", "claude-opus-4-7")
    monkeypatch.setenv("NEWS_RESEARCH_MAX_ITEMS", "5")
    monkeypatch.setenv("NEWS_RESEARCH_MAX_TURNS", "120")
    cfg = DaemonConfig.from_env()
    assert cfg.news_research_enabled is True
    assert cfg.news_research_model == "claude-opus-4-7"
    assert cfg.news_research_max_items == 5
    assert cfg.news_research_max_turns == 120


def test_news_research_max_items_must_be_positive(monkeypatch):
    _set_base(monkeypatch)
    monkeypatch.setenv("NEWS_RESEARCH_MAX_ITEMS", "0")
    with pytest.raises(ValueError, match="news_research_max_items"):
        DaemonConfig.from_env()
