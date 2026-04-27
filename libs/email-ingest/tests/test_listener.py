from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from email_ingest.bridge import BridgeConfig
from email_ingest.listener import ImapIdleListener
from email_ingest.state import EmailIngestStateDB
from tests._fake_imap import FakeImapServer, FakeMessage

SAMPLE = b"Message-ID: <a@x>\r\nSubject: [Plaud-AutoFlow] hi\r\n\r\nbody"


@pytest.fixture
async def setup(tmp_path: Path):
    server = FakeImapServer()
    await server.start()
    db = EmailIngestStateDB(tmp_path / "e.db")
    await db.init_db()
    cfg = BridgeConfig(
        host="127.0.0.1", port=server.port, user="user@x", password="pw",
        backoff_initial=0.05, backoff_max=0.2,
    )
    yield server, db, cfg
    await server.stop()


async def _run_until(listener: ImapIdleListener, condition, timeout: float = 2.0) -> None:
    # Drive the listener until `condition()` is true or we time out.
    task = asyncio.create_task(listener.run())
    try:
        start = asyncio.get_running_loop().time()
        while not condition():
            if asyncio.get_running_loop().time() - start > timeout:
                raise TimeoutError(f"condition not met within {timeout}s")
            await asyncio.sleep(0.02)
    finally:
        listener.stop()
        await asyncio.wait_for(task, timeout=1.0)


async def test_listener_delivers_new_message(setup) -> None:
    server, db, cfg = setup
    received: list[tuple[int, bytes]] = []

    async def on_new(uid: int, raw: bytes, headers: dict) -> None:
        received.append((uid, raw))

    listener = ImapIdleListener(cfg, db, on_new_email=on_new)
    task = asyncio.create_task(listener.run())
    await asyncio.sleep(0.2)
    await server.push_new_message(FakeMessage(uid=1, raw=SAMPLE))
    await _run_until_external(lambda: len(received) == 1, timeout=2.0)
    listener.stop()
    await asyncio.wait_for(task, timeout=1.0)
    assert received[0][0] == 1
    assert b"[Plaud-AutoFlow]" in received[0][1]


async def _run_until_external(condition, timeout: float = 2.0) -> None:
    start = asyncio.get_running_loop().time()
    while not condition():
        if asyncio.get_running_loop().time() - start > timeout:
            raise TimeoutError(f"condition not met within {timeout}s")
        await asyncio.sleep(0.02)


async def test_listener_persists_uidnext_checkpoint(setup) -> None:
    server, db, cfg = setup
    received: list[int] = []

    async def on_new(uid: int, raw: bytes, headers: dict) -> None:
        received.append(uid)

    listener = ImapIdleListener(cfg, db, on_new_email=on_new)
    task = asyncio.create_task(listener.run())
    await asyncio.sleep(0.2)
    await server.push_new_message(FakeMessage(uid=1, raw=SAMPLE))
    await _run_until_external(lambda: len(received) == 1, timeout=2.0)
    listener.stop()
    await asyncio.wait_for(task, timeout=1.0)

    checkpoint = await db.get_uidnext_checkpoint()
    assert checkpoint >= 2


async def test_listener_resync_on_reconnect(setup) -> None:
    server, db, cfg = setup
    received: list[int] = []

    async def on_new(uid: int, raw: bytes, headers: dict) -> None:
        received.append(uid)

    await db.set_uidnext_checkpoint(2)
    server.messages.append(FakeMessage(uid=1, raw=SAMPLE))
    server.messages.append(FakeMessage(uid=2, raw=SAMPLE.replace(b"<a@x>", b"<b@x>")))

    listener = ImapIdleListener(cfg, db, on_new_email=on_new)
    task = asyncio.create_task(listener.run())
    await _run_until_external(lambda: 2 in received, timeout=2.0)
    listener.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert 2 in received
    assert 1 not in received


async def test_listener_retries_after_transient_connection_error(tmp_path: Path) -> None:
    # Simulate a transient connection failure: first attempt raises
    # ImapConnectionError, second succeeds. Listener must recover without
    # manual intervention.
    from unittest.mock import AsyncMock, patch
    from email_ingest.errors import ImapConnectionError

    db = EmailIngestStateDB(tmp_path / "e.db")
    await db.init_db()

    server = FakeImapServer()
    await server.start()
    try:
        server.messages.append(FakeMessage(uid=1, raw=SAMPLE))
        cfg = BridgeConfig(
            host="127.0.0.1", port=server.port, user="user@x", password="pw",
            backoff_initial=0.02, backoff_max=0.05,
        )
        received: list[int] = []

        async def on_new(uid: int, raw: bytes, headers: dict) -> None:
            received.append(uid)

        listener = ImapIdleListener(cfg, db, on_new_email=on_new)

        # Fail once with a transient error, then fall through to the real
        # bridge on subsequent calls.
        real_connect = listener._bridge.connect
        call_count = 0

        def flaky_connect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                async def failing_ctx():
                    raise ImapConnectionError("simulated transient")
                    yield  # unreachable
                return _AsyncCtxRaising(ImapConnectionError("simulated transient"))
            return real_connect()

        listener._bridge.connect = flaky_connect  # type: ignore[method-assign]

        task = asyncio.create_task(listener.run())
        await _run_until_external(lambda: 1 in received, timeout=3.0)
        listener.stop()
        await asyncio.wait_for(task, timeout=1.0)
        assert call_count >= 2
        assert 1 in received
    finally:
        await server.stop()


class _AsyncCtxRaising:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *args):
        return False


async def test_persistent_failure_fires_after_threshold(tmp_path: Path) -> None:
    # No server listening — every connect attempt fails. Listener should
    # invoke on_persistent_failure after PERSISTENT_FAILURE_THRESHOLD cycles.
    db = EmailIngestStateDB(tmp_path / "e.db")
    await db.init_db()
    # Port 1 is reserved (ICMP) — guaranteed to refuse TCP. Keep backoff
    # tiny so the test exits quickly.
    cfg = BridgeConfig(
        host="127.0.0.1", port=1, user="u", password="p",
        timeout=0.2, backoff_initial=0.02, backoff_max=0.05,
    )
    alerts = 0

    async def on_persistent_failure() -> None:
        nonlocal alerts
        alerts += 1

    listener = ImapIdleListener(
        cfg, db, on_new_email=_noop, on_persistent_failure=on_persistent_failure,
    )
    task = asyncio.create_task(listener.run())
    await _run_until_external(lambda: alerts >= 1, timeout=10.0)
    listener.stop()
    await asyncio.wait_for(task, timeout=2.0)
    assert alerts >= 1


async def _noop(uid: int, raw: bytes, headers: dict) -> None:
    return None


@pytest.mark.asyncio
async def test_listener_logs_connect_and_idle_heartbeat(setup, caplog):
    """Listener emits an INFO connect log and a DEBUG idle-entry log."""
    import logging
    caplog.set_level(logging.DEBUG, logger="email_ingest.listener")

    server, db, cfg = setup

    async def noop(*_args, **_kwargs):
        return None

    listener = ImapIdleListener(
        cfg, db,
        on_new_email=noop,
    )

    task = asyncio.create_task(listener.run())
    # Wait long enough for connect + idle_start to fire
    await asyncio.sleep(0.3)
    listener.stop()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        task.cancel()

    records = [r for r in caplog.records if r.name == "email_ingest.listener"]
    info_msgs = [r.message for r in records if r.levelno == logging.INFO]
    debug_msgs = [r.message for r in records if r.levelno == logging.DEBUG]
    assert any("IMAP connected" in m for m in info_msgs), f"missing connect INFO log; got: {info_msgs}"
    assert any("Entering IDLE" in m for m in debug_msgs), f"missing idle DEBUG log; got: {debug_msgs}"
