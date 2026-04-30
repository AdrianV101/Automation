from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from email_ingest.bridge import BridgeConfig, ImapBridge
from email_ingest.errors import ImapAuthError
from tests._fake_imap import FakeImapServer, FakeMessage


@pytest.fixture
async def fake_server() -> FakeImapServer:
    server = FakeImapServer()
    await server.start()
    yield server
    await server.stop()


async def test_bridge_login_and_select(fake_server: FakeImapServer) -> None:
    cfg = BridgeConfig(
        host="127.0.0.1", port=fake_server.port,
        user=fake_server.username, password=fake_server.password,
    )
    bridge = ImapBridge(cfg)
    async with bridge.connect() as conn:
        uidnext = await conn.select_folder()
        assert uidnext >= 1
    assert fake_server.selected_folders == ["INBOX"]


async def test_bridge_fetch_raw(fake_server: FakeImapServer) -> None:
    fake_server.messages.append(FakeMessage(uid=1, raw=b"Subject: hi\r\n\r\nbody"))
    cfg = BridgeConfig(
        host="127.0.0.1", port=fake_server.port,
        user=fake_server.username, password=fake_server.password,
    )
    bridge = ImapBridge(cfg)
    async with bridge.connect() as conn:
        await conn.select_folder()
        raw = await conn.fetch_raw(uid=1)
        assert b"Subject: hi" in raw


async def test_bridge_select_named_folder(fake_server: FakeImapServer) -> None:
    cfg = BridgeConfig(
        host="127.0.0.1", port=fake_server.port,
        user=fake_server.username, password=fake_server.password,
    )
    bridge = ImapBridge(cfg)
    async with bridge.connect() as conn:
        uidnext = await conn.select_folder("News")
        assert uidnext >= 1
    assert "News" in fake_server.selected_folders


async def test_bridge_invokes_starttls_when_configured(
    fake_server: FakeImapServer,
) -> None:
    # STARTTLS upgrade is the default on Proton Bridge; verify the bridge
    # calls _do_starttls before LOGIN when `use_starttls=True`. (The
    # FakeImapServer is plain-TCP, so we patch _do_starttls to a no-op.)
    cfg = BridgeConfig(
        host="127.0.0.1", port=fake_server.port,
        user=fake_server.username, password=fake_server.password,
        use_starttls=True, ssl_verify=False,
    )
    bridge = ImapBridge(cfg)
    with patch(
        "email_ingest.bridge._do_starttls", new_callable=AsyncMock,
    ) as mock_starttls:
        async with bridge.connect():
            pass
    mock_starttls.assert_awaited_once()
    kwargs = mock_starttls.await_args.kwargs
    assert kwargs.get("verify") is False
    assert kwargs.get("server_hostname") == "127.0.0.1"


async def test_bridge_skips_starttls_by_default(
    fake_server: FakeImapServer,
) -> None:
    cfg = BridgeConfig(
        host="127.0.0.1", port=fake_server.port,
        user=fake_server.username, password=fake_server.password,
    )
    bridge = ImapBridge(cfg)
    with patch(
        "email_ingest.bridge._do_starttls", new_callable=AsyncMock,
    ) as mock_starttls:
        async with bridge.connect():
            pass
    mock_starttls.assert_not_awaited()


async def test_bridge_login_failure_raises_auth_error(
    fake_server: FakeImapServer,
) -> None:
    cfg = BridgeConfig(
        host="127.0.0.1", port=fake_server.port,
        user="wrong-user", password="wrong-password",
    )
    bridge = ImapBridge(cfg)
    with pytest.raises(ImapAuthError):
        async with bridge.connect():
            pass


def test_bridge_config_rejects_invalid_port() -> None:
    with pytest.raises(ValueError):
        BridgeConfig(host="127.0.0.1", port=0, user="u", password="p")
    with pytest.raises(ValueError):
        BridgeConfig(host="127.0.0.1", port=70000, user="u", password="p")


def test_bridge_config_rejects_bad_backoff() -> None:
    with pytest.raises(ValueError):
        BridgeConfig(
            host="127.0.0.1", port=143, user="u", password="p",
            backoff_multiplier=0.5,
        )


def test_bridge_config_rejects_ssl_verify_without_starttls() -> None:
    # ssl_verify=False with use_starttls=False means "TLS never happens"
    # dressed up as "TLS verification disabled" — reject the confusion.
    with pytest.raises(ValueError, match="ssl_verify"):
        BridgeConfig(
            host="127.0.0.1", port=143, user="u", password="p",
            use_starttls=False, ssl_verify=False,
        )
