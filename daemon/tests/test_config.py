import os
from pathlib import Path
from unittest.mock import patch

import pytest

from audio_ingest.config import DaemonConfig

# Patch load_dotenv to prevent .env file from polluting test environment
_no_dotenv = patch("audio_ingest.config.load_dotenv")


def _base_env() -> dict:
    """Minimal valid env vars for DaemonConfig.from_env()."""
    return {
        "TELEGRAM_BOT_TOKEN": "bot123",
        "TELEGRAM_CHAT_ID": "456",
        "PKM_VAULT_PATH": "/tmp/pkm",
    }


class TestDaemonConfig:
    """Test that DaemonConfig loads flat fields correctly."""

    def test_telegram_fields(self):
        env = _base_env()
        with _no_dotenv, patch.dict(os.environ, env, clear=True):
            config = DaemonConfig.from_env(env_file=None)
        assert config.telegram_bot_token == "bot123"
        assert config.telegram_chat_id == "456"

    def test_config_is_frozen(self):
        env = _base_env()
        with _no_dotenv, patch.dict(os.environ, env, clear=True):
            config = DaemonConfig.from_env(env_file=None)
        with pytest.raises(AttributeError):
            config.telegram_bot_token = "new"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            config.email_ingest_enabled = True  # type: ignore[misc]

    def test_pkm_vault_path(self):
        env = _base_env()
        with _no_dotenv, patch.dict(os.environ, env, clear=True):
            config = DaemonConfig.from_env(env_file=None)
        assert config.pkm_vault_path == Path("/tmp/pkm")


class TestConfigFromEnv:
    def test_missing_required_var_raises_with_name(self):
        env = {"TELEGRAM_BOT_TOKEN": "bot123"}
        with _no_dotenv, patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="TELEGRAM_CHAT_ID"):
                DaemonConfig.from_env(env_file=None)

    def test_invalid_int_env_var_includes_var_name(self):
        env = {**_base_env(), "IMAP_PORT": "not_a_number"}
        with _no_dotenv, patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="IMAP_PORT"):
                DaemonConfig.from_env(env_file=None)


def test_email_ingest_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c", "PKM_VAULT_PATH": "/tmp/vault"}
    with _no_dotenv, patch.dict(os.environ, env, clear=True):
        cfg = DaemonConfig.from_env(env_file=None)
    assert cfg.email_ingest_enabled is False
    assert cfg.imap_host == "127.0.0.1"
    assert cfg.imap_port == 1143
    assert cfg.vault_attachments_subdir == "99-Attachments/plaud"


def test_email_ingest_enabled_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("PKM_VAULT_PATH", "/tmp/vault")
    monkeypatch.setenv("EMAIL_INGEST_ENABLED", "true")
    monkeypatch.setenv("IMAP_USER", "imap-test@example.com")
    monkeypatch.setenv("IMAP_PASSWORD", "test-password")
    cfg = DaemonConfig.from_env()
    assert cfg.email_ingest_enabled is True
    assert cfg.imap_user == "imap-test@example.com"
    assert cfg.imap_password == "test-password"


def test_agent_inactivity_timeout_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "y")
    monkeypatch.setenv("PKM_VAULT_PATH", "/tmp/vault")
    cfg = DaemonConfig.from_env()
    assert cfg.agent_inactivity_timeout_s == 600.0
    assert cfg.max_concurrent_dispatch == 4


def test_agent_inactivity_timeout_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "y")
    monkeypatch.setenv("PKM_VAULT_PATH", "/tmp/vault")
    monkeypatch.setenv("AGENT_INACTIVITY_TIMEOUT_S", "300")
    monkeypatch.setenv("MAX_CONCURRENT_DISPATCH", "8")
    cfg = DaemonConfig.from_env()
    assert cfg.agent_inactivity_timeout_s == 300.0
    assert cfg.max_concurrent_dispatch == 8


def test_daemon_config_rejects_zero_inactivity_timeout() -> None:
    with pytest.raises(ValueError, match="agent_inactivity_timeout_s must be positive"):
        DaemonConfig(
            telegram_bot_token="x",
            telegram_chat_id="y",
            pkm_vault_path=Path("/tmp"),
            agent_inactivity_timeout_s=0.0,
        )


def test_daemon_config_rejects_zero_concurrent_dispatch() -> None:
    with pytest.raises(ValueError, match="max_concurrent_dispatch must be >= 1"):
        DaemonConfig(
            telegram_bot_token="x",
            telegram_chat_id="y",
            pkm_vault_path=Path("/tmp"),
            max_concurrent_dispatch=0,
        )


def _set_required_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "y")
    monkeypatch.setenv("PKM_VAULT_PATH", str(tmp_path))


class TestNewsDailyMasterConfig:
    def test_defaults_when_unset(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _set_required_env(monkeypatch, tmp_path)
        cfg = DaemonConfig.from_env()
        assert cfg.news_daily_master_enabled is False
        assert cfg.news_daily_master_local_time == "06:00"
        assert cfg.news_daily_master_backfill_days == 3
        assert cfg.news_daily_master_model == "claude-opus-4-7"
        assert cfg.news_daily_telegram_topic_id is None

    def test_reads_env_vars(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _set_required_env(monkeypatch, tmp_path)
        monkeypatch.setenv("NEWS_DAILY_MASTER_ENABLED", "true")
        monkeypatch.setenv("NEWS_DAILY_MASTER_LOCAL_TIME", "07:30")
        monkeypatch.setenv("NEWS_DAILY_MASTER_BACKFILL_DAYS", "5")
        monkeypatch.setenv("NEWS_DAILY_MASTER_MODEL", "claude-opus-4-7")
        monkeypatch.setenv("NEWS_DAILY_TELEGRAM_TOPIC_ID", "987")
        cfg = DaemonConfig.from_env()
        assert cfg.news_daily_master_enabled is True
        assert cfg.news_daily_master_local_time == "07:30"
        assert cfg.news_daily_master_backfill_days == 5
        assert cfg.news_daily_master_model == "claude-opus-4-7"
        assert cfg.news_daily_telegram_topic_id == 987

    def test_invalid_local_time_rejected(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _set_required_env(monkeypatch, tmp_path)
        monkeypatch.setenv("NEWS_DAILY_MASTER_ENABLED", "true")
        monkeypatch.setenv("NEWS_DAILY_MASTER_LOCAL_TIME", "25:00")
        with pytest.raises(ValueError, match="NEWS_DAILY_MASTER_LOCAL_TIME"):
            DaemonConfig.from_env()
