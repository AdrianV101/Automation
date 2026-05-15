import os
import pytest
from unittest.mock import patch
from automation_daemon.config import DaemonConfig


def test_clippings_defaults_off():
    c = DaemonConfig()
    assert c.clippings_enabled is False
    assert c.clippings_settle_seconds == 5
    assert c.clippings_reconcile_interval_seconds == 3600
    assert c.clippings_max_failed_retries == 3
    assert c.clippings_dir == "Clippings"
    assert c.clippings_model == "claude-opus-4-7"


def test_clippings_from_env(monkeypatch):
    monkeypatch.setenv("CLIPPINGS_ENABLED", "true")
    monkeypatch.setenv("CLIPPINGS_SETTLE_SECONDS", "9")
    monkeypatch.setenv("CLIPPINGS_DIR", "00-Inbox/Clippings")
    monkeypatch.setenv("CLIPPINGS_MODEL", "claude-opus-4-7")
    # Required by from_env()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setenv("PKM_VAULT_PATH", "/tmp/vault")
    c = DaemonConfig.from_env()
    assert c.clippings_enabled is True
    assert c.clippings_settle_seconds == 9
    assert c.clippings_dir == "00-Inbox/Clippings"
    assert c.clippings_model == "claude-opus-4-7"


def test_clippings_reconcile_interval_zero_rejected():
    with pytest.raises(ValueError, match="CLIPPINGS_RECONCILE_INTERVAL_SECONDS"):
        DaemonConfig(clippings_reconcile_interval_seconds=0)


def test_clippings_settle_negative_rejected():
    with pytest.raises(ValueError, match="CLIPPINGS_SETTLE_SECONDS"):
        DaemonConfig(clippings_settle_seconds=-1)


def test_clippings_max_failed_retries_negative_rejected():
    with pytest.raises(ValueError, match="CLIPPINGS_MAX_FAILED_RETRIES"):
        DaemonConfig(clippings_max_failed_retries=-1)
