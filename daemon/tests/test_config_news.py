from audio_ingest.config import DaemonConfig


def test_news_config_loads_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setenv("PKM_VAULT_PATH", "/tmp/vault")
    monkeypatch.setenv("NEWS_INGEST_ENABLED", "true")
    monkeypatch.setenv("NEWS_IMAP_FOLDER", "News")
    monkeypatch.setenv("NEWS_TELEGRAM_TOPIC_ID", "1234")
    cfg = DaemonConfig.from_env()
    assert cfg.news_ingest_enabled is True
    assert cfg.news_imap_folder == "News"
    assert cfg.news_telegram_topic_id == 1234


def test_news_config_defaults_to_disabled(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setenv("PKM_VAULT_PATH", "/tmp/vault")
    monkeypatch.delenv("NEWS_INGEST_ENABLED", raising=False)
    monkeypatch.delenv("NEWS_TELEGRAM_TOPIC_ID", raising=False)
    cfg = DaemonConfig.from_env()
    assert cfg.news_ingest_enabled is False
    assert cfg.news_telegram_topic_id is None
    assert cfg.news_imap_folder == "News"
