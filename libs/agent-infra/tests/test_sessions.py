"""Tests for agent_infra.sessions — SessionManager with SessionStore protocol."""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_infra import SessionManager, SessionResponse, SessionStore, TraceEvent
from agent_infra.sessions import _MAX_SESSIONS
from tests.conftest import (
    MockAssistantMessage,
    MockResultMessage,
    MockTextBlock,
    MockToolUseBlock,
    MockUserMessage,
    MockToolResultBlock,
    make_assistant_message,
    make_result_message,
)


# ---------------------------------------------------------------------------
# FakeSessionStore — in-memory dict implementation of SessionStore
# ---------------------------------------------------------------------------


class FakeSessionStore:
    def __init__(self):
        self._store: dict[str, str] = {}

    async def get_session_id(self, key: str) -> str | None:
        return self._store.get(key)

    async def save_session_id(self, key: str, session_id: str) -> None:
        self._store[key] = session_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client(
    text_blocks: list[str] | None = None,
    tool_blocks: list[tuple[str, dict]] | None = None,
    session_id: str = "sess-123",
) -> tuple[AsyncMock, type]:
    """Create a fully wired mock ClaudeSDKClient."""
    client = AsyncMock()
    result_msg = make_result_message(session_id=session_id)

    async def receive():
        yield make_assistant_message(
            text_blocks=text_blocks or ["ok"],
            tool_blocks=tool_blocks,
        )
        yield result_msg

    client.query = AsyncMock()
    client.receive_response = receive
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client, type(result_msg)


def _patch_sessions(mock_client, result_msg_cls):
    """Return a context manager that patches all SDK types for isinstance checks."""
    patches = [
        patch("agent_infra.sessions.ClaudeSDKClient", return_value=mock_client),
        patch("agent_infra.sessions.AssistantMessage", MockAssistantMessage),
        patch("agent_infra.sessions.TextBlock", MockTextBlock),
        patch("agent_infra.sessions.ToolUseBlock", MockToolUseBlock),
        patch("agent_infra.sessions.UserMessage", MockUserMessage),
        patch("agent_infra.sessions.ToolResultBlock", MockToolResultBlock),
        patch("agent_infra.sessions.ResultMessage", result_msg_cls),
    ]
    stack = contextlib.ExitStack()
    for p in patches:
        stack.enter_context(p)
    return stack


# ---------------------------------------------------------------------------
# Tests — SessionStore protocol
# ---------------------------------------------------------------------------


class TestSessionStoreProtocol:
    def test_fake_store_implements_protocol(self):
        """FakeSessionStore is a valid SessionStore."""
        store = FakeSessionStore()
        assert isinstance(store, SessionStore)


# ---------------------------------------------------------------------------
# Tests — SessionManager
# ---------------------------------------------------------------------------


class TestSessionManager:
    @pytest.fixture
    def store(self):
        return FakeSessionStore()

    @pytest.fixture
    def manager(self, store, tmp_path):
        return SessionManager(
            session_store=store,
            pkm_vault_path=tmp_path / "pkm",
        )

    @pytest.mark.asyncio
    async def test_send_new_creates_session(self, manager):
        """First message to a new session_key creates a ClaudeSDKClient."""
        mock_client, result_cls = _make_mock_client(text_blocks=["Hello!"])

        with _patch_sessions(mock_client, result_cls):
            response = await manager.send(
                session_key="thread-42",
                message="What is MyProject?",
                system_prompt="You are a PKM agent.",
            )

        assert response.text == "Hello!"
        assert response.session_id == "sess-123"
        mock_client.query.assert_called_once_with("What is MyProject?")

    @pytest.mark.asyncio
    async def test_send_to_existing_session_reuses_client(self, manager):
        """Follow-up messages reuse the cached ClaudeSDKClient."""
        mock_client, result_cls = _make_mock_client()

        call_count = 0

        async def receive():
            nonlocal call_count
            call_count += 1
            yield make_assistant_message(text_blocks=[f"Response {call_count}"])
            yield make_result_message(session_id="sess-123")

        mock_client.receive_response = receive

        with patch("agent_infra.sessions.ClaudeSDKClient", return_value=mock_client) as mock_csc, \
             patch("agent_infra.sessions.AssistantMessage", MockAssistantMessage), \
             patch("agent_infra.sessions.TextBlock", MockTextBlock), \
             patch("agent_infra.sessions.ToolUseBlock", MockToolUseBlock), \
             patch("agent_infra.sessions.UserMessage", MockUserMessage), \
             patch("agent_infra.sessions.ToolResultBlock", MockToolResultBlock), \
             patch("agent_infra.sessions.ResultMessage", result_cls):
            await manager.send(
                session_key="thread-42", message="First",
                system_prompt="sys",
            )
            await manager.send(
                session_key="thread-42", message="Follow-up",
                system_prompt="sys",
            )

        # ClaudeSDKClient created only once
        assert mock_csc.call_count == 1
        # But query called twice
        assert mock_client.query.call_count == 2

    @pytest.mark.asyncio
    async def test_different_session_keys_create_separate_clients(self, manager):
        """Different session_keys get independent ClaudeSDKClient instances."""
        mock_client, result_cls = _make_mock_client()

        with patch("agent_infra.sessions.ClaudeSDKClient", return_value=mock_client) as mock_csc, \
             patch("agent_infra.sessions.AssistantMessage", MockAssistantMessage), \
             patch("agent_infra.sessions.TextBlock", MockTextBlock), \
             patch("agent_infra.sessions.ToolUseBlock", MockToolUseBlock), \
             patch("agent_infra.sessions.UserMessage", MockUserMessage), \
             patch("agent_infra.sessions.ToolResultBlock", MockToolResultBlock), \
             patch("agent_infra.sessions.ResultMessage", result_cls):
            await manager.send(
                session_key="thread-1", message="Hello",
                system_prompt="sys",
            )
            await manager.send(
                session_key="thread-2", message="Hi",
                system_prompt="sys",
            )

        # Two separate ClaudeSDKClient instances created
        assert mock_csc.call_count == 2

    @pytest.mark.asyncio
    async def test_session_id_stored_in_session_store(self, manager, store):
        """After a successful send, session_id is persisted in SessionStore."""
        mock_client, result_cls = _make_mock_client(
            text_blocks=["ok"], session_id="sess-abc",
        )

        with _patch_sessions(mock_client, result_cls):
            await manager.send(
                session_key="thread-42", message="test",
                system_prompt="sys",
            )

        assert store._store.get("thread-42") == "sess-abc"

    @pytest.mark.asyncio
    async def test_resumes_session_from_store(self, manager, store):
        """If SessionStore has a session_id for the key, build_options gets resume."""
        # Pre-populate a session in the store
        store._store["thread-42"] = "old-sess"

        mock_client, result_cls = _make_mock_client()

        with patch("agent_infra.sessions.ClaudeSDKClient", return_value=mock_client) as mock_csc, \
             patch("agent_infra.sessions.AssistantMessage", MockAssistantMessage), \
             patch("agent_infra.sessions.TextBlock", MockTextBlock), \
             patch("agent_infra.sessions.ToolUseBlock", MockToolUseBlock), \
             patch("agent_infra.sessions.UserMessage", MockUserMessage), \
             patch("agent_infra.sessions.ToolResultBlock", MockToolResultBlock), \
             patch("agent_infra.sessions.ResultMessage", result_cls):
            await manager.send(
                session_key="thread-42", message="follow up",
                system_prompt="sys",
            )

        # Verify the options passed to ClaudeSDKClient included resume
        call_kwargs = mock_csc.call_args
        opts = call_kwargs.kwargs.get("options") or call_kwargs.args[0]
        assert opts.resume == "old-sess"
        assert opts.continue_conversation is True

    @pytest.mark.asyncio
    async def test_files_written_collected(self, manager):
        """Tool use blocks with vault_write paths are collected in files_written."""
        mock_client, result_cls = _make_mock_client(
            text_blocks=["Wrote note."],
            tool_blocks=[
                ("mcp__obsidian-pkm__vault_write", {"path": "00-Inbox/note.md"}),
            ],
        )

        with _patch_sessions(mock_client, result_cls):
            response = await manager.send(
                session_key="thread-1", message="write a note",
                system_prompt="sys",
            )

        assert response.files_written == ["00-Inbox/note.md"]

    @pytest.mark.asyncio
    async def test_no_response_text_shows_placeholder(self, manager):
        """When agent produces no text blocks, response shows (no response)."""
        mock_client, result_cls = _make_mock_client(text_blocks=[])

        async def receive():
            yield make_result_message(session_id="sess-1")

        mock_client.receive_response = receive

        with _patch_sessions(mock_client, result_cls):
            response = await manager.send(
                session_key="thread-1", message="test",
                system_prompt="sys",
            )

        assert response.text == "(no response)"

    @pytest.mark.asyncio
    async def test_error_during_collect_returns_error_response(self, manager):
        """If receive_response raises, return an error SessionResponse."""
        mock_client, result_cls = _make_mock_client()

        async def exploding_receive():
            raise RuntimeError("connection lost")
            yield  # noqa: unreachable

        mock_client.receive_response = exploding_receive

        with _patch_sessions(mock_client, result_cls):
            response = await manager.send(
                session_key="thread-1", message="test",
                system_prompt="sys",
            )

        assert response.error is not None
        assert "Session error" in response.text

    @pytest.mark.asyncio
    async def test_close_session_removes_client(self, manager):
        """close_session removes the client from cache and calls __aexit__."""
        mock_client, result_cls = _make_mock_client()

        with _patch_sessions(mock_client, result_cls):
            await manager.send(
                session_key="thread-1", message="test",
                system_prompt="sys",
            )

        assert "thread-1" in manager._clients
        await manager.close_session("thread-1")
        assert "thread-1" not in manager._clients
        mock_client.__aexit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_session_nonexistent_is_noop(self, manager):
        """Closing a session that doesn't exist is a no-op."""
        await manager.close_session("nonexistent")
        # Should not raise

    @pytest.mark.asyncio
    async def test_close_all(self, manager):
        """close_all removes all cached clients."""
        mock_client1, result_cls = _make_mock_client()
        mock_client2, _ = _make_mock_client(session_id="sess-2")

        with _patch_sessions(mock_client1, result_cls):
            await manager.send(
                session_key="thread-1", message="a",
                system_prompt="sys",
            )

        # Manually inject a second client
        manager._clients["thread-2"] = mock_client2

        await manager.close_all()
        assert len(manager._clients) == 0

    @pytest.mark.asyncio
    async def test_query_failure_evicts_client(self, manager):
        """If client.query() raises, the client is evicted from cache."""
        mock_client, result_cls = _make_mock_client()
        mock_client.query = AsyncMock(side_effect=RuntimeError("connection lost"))

        with _patch_sessions(mock_client, result_cls):
            response = await manager.send(
                session_key="thread-1", message="test",
                system_prompt="sys",
            )

        assert response.error is not None
        assert "Client query failed" in response.error
        assert "thread-1" not in manager._clients
        mock_client.__aexit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_evicts_oldest_when_at_capacity(self, manager):
        """When _MAX_SESSIONS is reached, oldest session is evicted."""
        mock_client, result_cls = _make_mock_client()

        with _patch_sessions(mock_client, result_cls):
            # Fill to capacity with mock clients
            for i in range(3):
                manager._clients[f"old-{i}"] = AsyncMock()

        with patch("agent_infra.sessions._MAX_SESSIONS", 3):
            with _patch_sessions(mock_client, result_cls):
                await manager.send(
                    session_key="new-session", message="hello",
                    system_prompt="sys",
                )

        # Oldest session was evicted
        assert "old-0" not in manager._clients
        assert "new-session" in manager._clients

    @pytest.mark.asyncio
    async def test_partial_results_preserved_on_collect_error(self, manager):
        """If receive_response raises mid-stream, partial text is preserved."""
        mock_client, result_cls = _make_mock_client()

        async def partial_then_explode():
            yield make_assistant_message(text_blocks=["Partial result here."])
            raise RuntimeError("stream cut off")

        mock_client.receive_response = partial_then_explode

        with _patch_sessions(mock_client, result_cls):
            response = await manager.send(
                session_key="thread-1", message="test",
                system_prompt="sys",
            )

        assert response.error is not None
        assert "Partial result here." in response.text


@pytest.mark.asyncio
async def test_send_inactivity_timeout_evicts_session_and_returns_error():
    """When the agent goes silent past the timeout, the session is evicted
    and SessionResponse carries an error message instead of hanging."""
    from agent_infra import SessionManager

    store = FakeSessionStore()
    mgr = SessionManager(store, Path("/tmp/vault"))

    # Mock a ClaudeSDKClient whose receive_response() hangs forever
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.query = AsyncMock(return_value=None)

    async def hang_forever():
        await asyncio.sleep(60)
        if False:
            yield  # never reached, but makes this an async generator

    fake_client.receive_response = MagicMock(return_value=hang_forever())

    with patch("agent_infra.sessions.ClaudeSDKClient", return_value=fake_client):
        resp = await mgr.send(
            session_key="t1", message="hi", system_prompt="sys",
            inactivity_timeout_s=0.1,
        )

    assert resp.error is not None
    assert "inactivity" in resp.error.lower() or "timeout" in resp.error.lower()
    assert "t1" not in mgr._clients  # evicted
    fake_client.__aexit__.assert_called()  # subprocess cleanup


@pytest.mark.asyncio
async def test_send_aenter_failure_is_logged(caplog):
    """If SDK __aenter__ fails, the exception is logged before propagating."""
    import logging as stdlib_logging
    from agent_infra import SessionManager

    store = FakeSessionStore()
    mgr = SessionManager(store, Path("/tmp/vault"))

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(side_effect=RuntimeError("subprocess failed to start"))

    caplog.set_level(stdlib_logging.ERROR, logger="agent_infra.sessions")

    with patch("agent_infra.sessions.ClaudeSDKClient", return_value=fake_client):
        with pytest.raises(RuntimeError, match="subprocess failed"):
            await mgr.send(session_key="s1", message="hi", system_prompt="sys")

    assert any(
        "Failed to create SDK client" in r.message and "s1" in r.message
        for r in caplog.records
    ), "expected log entry for __aenter__ failure"
    assert "s1" not in mgr._clients  # must not be cached on failure


@pytest.mark.asyncio
async def test_send_query_failure_eviction_is_bounded(caplog):
    """When client.query() fails, close_session is called with a timeout so a
    hung transport cannot re-create the original 18-hour-hang scenario."""
    import logging as stdlib_logging
    from agent_infra import SessionManager

    store = FakeSessionStore()
    mgr = SessionManager(store, Path("/tmp/vault"))

    hang_event = asyncio.Event()  # never set

    async def hanging_aexit(*_):
        await hang_event.wait()

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.query = AsyncMock(side_effect=RuntimeError("query boom"))
    fake_client.__aexit__ = hanging_aexit

    caplog.set_level(stdlib_logging.WARNING, logger="agent_infra.sessions")

    with patch("agent_infra.sessions.ClaudeSDKClient", return_value=fake_client):
        resp = await mgr.send(
            session_key="q1", message="hi", system_prompt="sys",
        )

    assert resp.error is not None
    assert "q1" not in mgr._clients
    assert any("Timeout closing session" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_close_all_skips_session_that_hangs_in_aexit(caplog):
    """If one client's __aexit__ hangs, close_all must time out and proceed
    to the next client; subprocess cleanup is the next layer's responsibility."""
    import logging as stdlib_logging

    store = FakeSessionStore()
    mgr = SessionManager(store, Path("/tmp/vault"))

    hang_event = asyncio.Event()  # never set

    async def hanging_aexit(*_args):
        await hang_event.wait()  # blocks forever

    async def fast_aexit(*_args):
        return None

    hanging_client = MagicMock()
    hanging_client.__aexit__ = hanging_aexit

    fast_client = MagicMock()
    fast_client.__aexit__ = AsyncMock(side_effect=fast_aexit)

    mgr._clients["hung"] = hanging_client
    mgr._clients["fast"] = fast_client

    caplog.set_level(stdlib_logging.WARNING, logger="agent_infra.sessions")

    await mgr.close_all(per_session_timeout_s=0.05)

    # Both clients must be removed (the hung one popped after timeout)
    assert "hung" not in mgr._clients
    assert "fast" not in mgr._clients
    # Fast client got its __aexit__ called normally
    fast_client.__aexit__.assert_called_once()
    # Operator gets a warning about the timeout
    assert any(
        "Timeout closing session" in r.message and "hung" in r.message
        for r in caplog.records
    ), "expected timeout warning for hung session"


@pytest.mark.asyncio
async def test_concurrent_send_same_key_creates_client_once():
    """Two concurrent send() calls for the same session_key must create exactly
    one ClaudeSDKClient; the second call reuses the client the first created."""
    store = FakeSessionStore()
    mgr = SessionManager(store, Path("/tmp/vault"))

    mock_client, result_cls = _make_mock_client(text_blocks=["ok"])

    creation_count = 0
    original_aenter = mock_client.__aenter__

    async def counting_aenter(*args, **kwargs):
        nonlocal creation_count
        creation_count += 1
        return await original_aenter(*args, **kwargs)

    mock_client.__aenter__ = counting_aenter

    with _patch_sessions(mock_client, result_cls):
        # Launch two concurrent sends for the same key
        results = await asyncio.gather(
            mgr.send(session_key="shared-key", message="msg1", system_prompt="sys"),
            mgr.send(session_key="shared-key", message="msg2", system_prompt="sys"),
        )

    # __aenter__ called exactly once — no double-creation
    assert creation_count == 1, f"expected 1 __aenter__ call, got {creation_count}"
    # Both sends completed successfully
    assert all(r.text == "ok" for r in results)
    # query was called twice (once per send)
    assert mock_client.query.call_count == 2


@pytest.mark.asyncio
async def test_close_all_total_timeout_pops_remaining_sessions(caplog):
    """If all sessions hang in __aexit__, close_all returns within total_timeout_s
    and pops the remaining sessions rather than blocking indefinitely."""
    import logging as stdlib_logging

    store = FakeSessionStore()
    mgr = SessionManager(store, Path("/tmp/vault"))

    hang_event = asyncio.Event()  # never set

    async def hanging_aexit(*_args):
        await hang_event.wait()

    for i in range(5):
        client = MagicMock()
        client.__aexit__ = hanging_aexit
        mgr._clients[f"sess-{i}"] = client

    caplog.set_level(stdlib_logging.WARNING, logger="agent_infra.sessions")

    # per-session timeout > total: total must win
    await mgr.close_all(per_session_timeout_s=10.0, total_timeout_s=0.1)

    # All sessions must be cleared
    assert len(mgr._clients) == 0, "expected all sessions to be cleared after total timeout"
    # Warning must be emitted
    assert any(
        "total timeout" in r.message.lower() or "Timeout closing session" in r.message
        for r in caplog.records
    ), "expected a timeout warning"
