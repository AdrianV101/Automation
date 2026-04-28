"""Integration test: TelegramInterface command -> topic creation -> session -> response.

Tests the daemon-specific wiring (prompts, tools, status provider) with the library's
TelegramInterface class.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_infra import SessionManager, SessionResponse
from audio_ingest.command_config import build_daemon_commands
from audio_ingest.prompts import build_ask_system_prompt, build_note_system_prompt
from audio_ingest.tools import TOOLS_ASK, TOOLS_TASK
from telegram_interface import BotConfig, TelegramInterface, ThreadStore


def _make_tii(session_mgr: SessionManager, thread_store: ThreadStore, vault_path: Path) -> TelegramInterface:
    return TelegramInterface(
        bot_config=BotConfig(bot_token="test", chat_id="123"),
        commands=build_daemon_commands(vault_path),
        session_manager=session_mgr,
        thread_store=thread_store,
    )


@pytest.mark.asyncio
async def test_command_creates_topic_session_and_responds(tmp_path):
    """Full /ask flow: topic created, ack sent in topic, session used, response sent."""
    session_mgr = AsyncMock(spec=SessionManager)
    session_mgr.send.return_value = SessionResponse(
        text="Bob mentioned Q2 for funding.",
        session_id="sess-1",
    )
    thread_store = AsyncMock(spec=ThreadStore)
    tii = _make_tii(session_mgr, thread_store, tmp_path)

    with patch("telegram_interface.commands.create_forum_topic", return_value=555) as mock_topic, \
         patch("telegram_interface.commands.send_message_return_id", return_value=42) as mock_send:
        await tii.dispatch_command("/ask what did Bob say about funding?")

        # 1. Topic created with correct name
        mock_topic.assert_called_once()
        topic_name = mock_topic.call_args[0][0]
        assert topic_name.startswith("Ask:")

        # 2. Echo + ack + final response sent inside the topic
        assert mock_send.call_count == 3
        echo_call, ack_call, response_call = mock_send.call_args_list
        assert echo_call[0][0] == "what did Bob say about funding?"
        assert echo_call[1]["thread_id"] == 555
        assert ack_call[0][0] == "Thinking..."
        assert ack_call[1]["thread_id"] == 555

        # 3. session_manager.send called with correct params
        session_mgr.send.assert_called_once()
        call_kwargs = session_mgr.send.call_args[1]
        assert call_kwargs["session_key"] == "thread-555"
        assert call_kwargs["message"] == "what did Bob say about funding?"
        assert "command" not in call_kwargs
        assert "thread_id" not in call_kwargs

        # 4. Final response sent as new message (last in topic)
        assert "Bob mentioned Q2" in response_call[0][0]
        assert response_call[1]["thread_id"] == 555


@pytest.mark.asyncio
async def test_note_creates_topic_with_note_prefix(tmp_path):
    """/note flow: topic name starts with 'Note:', session uses note system prompt."""
    session_mgr = AsyncMock(spec=SessionManager)
    session_mgr.send.return_value = SessionResponse(
        text="Saved to 00-Inbox/fleeting.md",
        session_id="sess-2",
    )
    thread_store = AsyncMock(spec=ThreadStore)
    tii = _make_tii(session_mgr, thread_store, tmp_path)

    with patch("telegram_interface.commands.create_forum_topic", return_value=888) as mock_topic, \
         patch("telegram_interface.commands.send_message_return_id", return_value=10) as mock_send:
        await tii.dispatch_command("/note buy milk for Carol")

        # Topic name starts with "Note:"
        topic_name = mock_topic.call_args[0][0]
        assert topic_name.startswith("Note:")
        assert "buy milk" in topic_name

        # Echo + ack + final response sent inside topic
        assert mock_send.call_count == 3
        echo_call, ack_call, _response_call = mock_send.call_args_list
        assert echo_call[0][0] == "buy milk for Carol"
        assert echo_call[1]["thread_id"] == 888
        assert ack_call[0][0] == "Got it, routing..."

        # session_manager.send called with correct session key
        call_kwargs = session_mgr.send.call_args[1]
        assert call_kwargs["session_key"] == "thread-888"
        assert "command" not in call_kwargs


@pytest.mark.asyncio
async def test_topic_failure_degrades_gracefully(tmp_path):
    """When create_forum_topic returns None, dispatch still works with general- key."""
    session_mgr = AsyncMock(spec=SessionManager)
    session_mgr.send.return_value = SessionResponse(
        text="Noted.",
        session_id="sess-3",
    )
    thread_store = AsyncMock(spec=ThreadStore)
    tii = _make_tii(session_mgr, thread_store, tmp_path)

    with patch("telegram_interface.commands.create_forum_topic", return_value=None), \
         patch("telegram_interface.commands.send_message_return_id", return_value=5) as mock_send:
        await tii.dispatch_command("/ask where is the config?")

        # Ack + final response sent without thread_id (general chat)
        assert mock_send.call_count == 2
        assert mock_send.call_args_list[0][1].get("thread_id") is None

        # session_manager.send gets general- key (no thread_id param)
        call_kwargs = session_mgr.send.call_args[1]
        assert call_kwargs["session_key"] == "general-ask"
        assert "thread_id" not in call_kwargs


@pytest.mark.asyncio
async def test_status_bypasses_sessions_entirely(tmp_path):
    """/status does NOT create topic, does NOT use session_manager."""
    session_mgr = AsyncMock(spec=SessionManager)
    thread_store = AsyncMock(spec=ThreadStore)
    tii = _make_tii(session_mgr, thread_store, tmp_path)

    with patch("telegram_interface.commands.create_forum_topic") as mock_topic, \
         patch("telegram_interface.commands.send_message_return_id") as mock_send:
        await tii.dispatch_command("/status")

        # No topic created
        mock_topic.assert_not_called()

        # session_manager NOT used
        session_mgr.send.assert_not_called()

        # Status message sent (no provider configured, so default message)
        mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_different_commands_get_different_system_prompts(tmp_path):
    """Verify /ask uses ask prompt and /note uses note prompt."""
    thread_store = AsyncMock(spec=ThreadStore)

    captured_prompts: dict[str, str] = {}

    for cmd, expected_command in [
        ("/ask what is life?", "ask"),
        ("/note remember to call Carol", "note"),
    ]:
        session_mgr = AsyncMock(spec=SessionManager)
        session_mgr.send.return_value = SessionResponse(text="ok", session_id="s1")
        tii = _make_tii(session_mgr, thread_store, tmp_path)

        with patch("telegram_interface.commands.create_forum_topic", return_value=100), \
             patch("telegram_interface.commands.send_message_return_id", return_value=1):
            await tii.dispatch_command(cmd)

            call_kwargs = session_mgr.send.call_args[1]
            captured_prompts[expected_command] = call_kwargs["system_prompt"]

    assert captured_prompts["ask"] == build_ask_system_prompt(tmp_path)
    assert captured_prompts["note"] == build_note_system_prompt(tmp_path)
    assert captured_prompts["ask"] != captured_prompts["note"]
