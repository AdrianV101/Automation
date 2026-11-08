from datetime import date

import pytest

from automation_daemon.config import DaemonConfig

_PLAUD_KEYS = (
    "ENABLE_PLAUD_MCP_INGEST",
    "PLAUD_MCP_SWITCHOVER_DATE",
    "PLAUD_MCP_PACKAGE",
    "PLAUD_MCP_POLL_INTERVAL_S",
    "PLAUD_MCP_LABEL_QUIET_S",
    "PLAUD_MCP_UNLABELLED_FALLBACK_S",
    "PLAUD_MCP_WINDOW_DAYS",
)


@pytest.fixture
def base_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setenv("PKM_VAULT_PATH", "/tmp/vault")
    for key in _PLAUD_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_defaults_when_disabled(base_env: pytest.MonkeyPatch) -> None:
    cfg = DaemonConfig.from_env()
    assert cfg.plaud_mcp_enabled is False
    assert cfg.plaud_mcp_switchover_date is None
    assert cfg.plaud_mcp_package == "@plaud-ai/mcp@0.3.14"
    assert cfg.plaud_mcp_poll_interval_s == 300
    assert cfg.plaud_mcp_label_quiet_s == 600
    assert cfg.plaud_mcp_unlabelled_fallback_s == 86400
    assert cfg.plaud_mcp_window_days == 14


def test_enabled_without_switchover_date_raises(base_env: pytest.MonkeyPatch) -> None:
    base_env.setenv("ENABLE_PLAUD_MCP_INGEST", "true")
    with pytest.raises(ValueError, match="PLAUD_MCP_SWITCHOVER_DATE"):
        DaemonConfig.from_env()


def test_malformed_switchover_date_raises(base_env: pytest.MonkeyPatch) -> None:
    base_env.setenv("ENABLE_PLAUD_MCP_INGEST", "true")
    base_env.setenv("PLAUD_MCP_SWITCHOVER_DATE", "07/11/2026")
    with pytest.raises(ValueError, match="PLAUD_MCP_SWITCHOVER_DATE"):
        DaemonConfig.from_env()


@pytest.mark.parametrize(
    "package",
    [
        "@plaud-ai/mcp@latest",
        "@plaud-ai/mcp@LATEST",
        "@plaud-ai/mcp@next",
        "@plaud-ai/mcp@^0.3.14",
        "@plaud-ai/mcp@0.x",
        "@plaud-ai/mcp",
    ],
)
def test_unpinned_package_raises(base_env: pytest.MonkeyPatch, package: str) -> None:
    base_env.setenv("ENABLE_PLAUD_MCP_INGEST", "true")
    base_env.setenv("PLAUD_MCP_SWITCHOVER_DATE", "2026-11-07")
    base_env.setenv("PLAUD_MCP_PACKAGE", package)
    with pytest.raises(ValueError, match="PLAUD_MCP_PACKAGE"):
        DaemonConfig.from_env()


def test_valid_full_config_parses(base_env: pytest.MonkeyPatch) -> None:
    base_env.setenv("ENABLE_PLAUD_MCP_INGEST", "true")
    base_env.setenv("PLAUD_MCP_SWITCHOVER_DATE", "2026-11-07")
    base_env.setenv("PLAUD_MCP_PACKAGE", "@plaud-ai/mcp@0.3.15")
    base_env.setenv("PLAUD_MCP_POLL_INTERVAL_S", "120")
    base_env.setenv("PLAUD_MCP_LABEL_QUIET_S", "900")
    base_env.setenv("PLAUD_MCP_UNLABELLED_FALLBACK_S", "43200")
    base_env.setenv("PLAUD_MCP_WINDOW_DAYS", "7")
    cfg = DaemonConfig.from_env()
    assert cfg.plaud_mcp_enabled is True
    assert cfg.plaud_mcp_switchover_date == date(2026, 11, 7)
    assert cfg.plaud_mcp_package == "@plaud-ai/mcp@0.3.15"
    assert cfg.plaud_mcp_poll_interval_s == 120
    assert cfg.plaud_mcp_label_quiet_s == 900
    assert cfg.plaud_mcp_unlabelled_fallback_s == 43200
    assert cfg.plaud_mcp_window_days == 7


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("PLAUD_MCP_POLL_INTERVAL_S", "0"),
        ("PLAUD_MCP_POLL_INTERVAL_S", "-5"),
        ("PLAUD_MCP_WINDOW_DAYS", "0"),
        ("PLAUD_MCP_WINDOW_DAYS", "-1"),
        ("PLAUD_MCP_LABEL_QUIET_S", "-1"),
        ("PLAUD_MCP_UNLABELLED_FALLBACK_S", "-1"),
    ],
)
def test_out_of_range_numeric_raises(
    base_env: pytest.MonkeyPatch, key: str, value: str,
) -> None:
    base_env.setenv(key, value)
    with pytest.raises(ValueError, match=key):
        DaemonConfig.from_env()


def test_zero_quiet_and_fallback_allowed(base_env: pytest.MonkeyPatch) -> None:
    base_env.setenv("PLAUD_MCP_LABEL_QUIET_S", "0")
    base_env.setenv("PLAUD_MCP_UNLABELLED_FALLBACK_S", "0")
    cfg = DaemonConfig.from_env()
    assert cfg.plaud_mcp_label_quiet_s == 0
    assert cfg.plaud_mcp_unlabelled_fallback_s == 0


def test_prerelease_version_accepted(base_env: pytest.MonkeyPatch) -> None:
    base_env.setenv("PLAUD_MCP_PACKAGE", "@plaud-ai/mcp@1.0.0-beta.2")
    assert DaemonConfig.from_env().plaud_mcp_package == "@plaud-ai/mcp@1.0.0-beta.2"
