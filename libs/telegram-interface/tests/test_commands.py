"""Tests for telegram_interface.commands — parse_command, TelegramInterface dispatch, and topic follow-ups."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent_infra import SessionResponse
from telegram_interface import ThreadRecord
from telegram_interface.commands import (
    TelegramInterface,
    _default_file_formatter,
    _topic_name,
    parse_command,
)
from telegram_interface.types import BotConfig, CommandConfig, StatusProvider, StatusSection

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

TEST_COMMANDS = [
    CommandConfig(name="note", system_prompt="Note prompt", tools=["vault_write"], description="Capture a note"),
    CommandConfig(name="ask", system_prompt="Ask prompt", tools=["vault_read"], description="Query PKM"),
    CommandConfig(name="task", system_prompt="Task prompt", tools=["vault_write"], description="Log a task"),
    CommandConfig(name="chat", system_prompt="Chat prompt", tools=["vault_read"], description="Chat"),
]

VALID = {cmd.name for cmd in TEST_COMMANDS} | {"status"}

BOT = BotConfig(bot_token="bot-token", chat_id="123")


def _make_interface(
    *,
    status_provider: StatusProvider | None = None,
    file_formatter=None,
) -> TelegramInterface:
    session_mgr = AsyncMock()
    thread_store = AsyncMock()
    return TelegramInterface(
        BOT,
        TEST_COMMANDS,
        session_mgr,
        thread_store,
        status_provider=status_provider,
        file_formatter=file_formatter,
    )


# ---------------------------------------------------------------------------
# parse_command tests
# ---------------------------------------------------------------------------


class TestParseCommand:
    def test_note_command(self):
        assert parse_command("/note buy milk", VALID) == ("note", "buy milk")

    def test_ask_command(self):
        assert parse_command("/ask what did Bob say?", VALID) == ("ask", "what did Bob say?")

    def test_task_command(self):
        assert parse_command("/task email Bob about pitch deck", VALID) == ("task", "email Bob about pitch deck")

    def test_status_command(self):
        assert parse_command("/status", VALID) == ("status", "")

    def test_status_ignores_args(self):
        assert parse_command("/status extra stuff", VALID) == ("status", "")

    def test_unknown_command(self):
        assert parse_command("/foo bar", VALID) == ("unknown", "")

    def test_no_slash_prefix(self):
        assert parse_command("hello world", VALID) == ("unknown", "")

    def test_empty_string(self):
        assert parse_command("", VALID) == ("unknown", "")

    def test_command_no_args(self):
        assert parse_command("/note", VALID) == ("note", "")

    def test_command_preserves_whitespace_in_args(self):
        assert parse_command("/note   lots  of   spaces", VALID) == ("note", "lots  of   spaces")

    def test_case_insensitive_command(self):
        assert parse_command("/NOTE buy milk", VALID) == ("note", "buy milk")

    def test_chat_command(self):
        assert parse_command("/chat hello there", VALID) == ("chat", "hello there")

    def test_chat_no_args(self):
        assert parse_command("/chat", VALID) == ("chat", "")

    def test_custom_valid_commands(self):
        """parse_command only accepts commands in the provided valid set."""
        custom = {"deploy", "rollback"}
        assert parse_command("/deploy prod", custom) == ("deploy", "prod")
        assert parse_command("/note buy milk", custom) == ("unknown", "")


# ---------------------------------------------------------------------------
# _topic_name tests
# ---------------------------------------------------------------------------


class TestTopicName:
    def test_short_args(self):
        assert _topic_name("note", "buy milk") == "Note: buy milk"

    def test_long_args_truncated(self):
        long_text = "a" * 50
        result = _topic_name("note", long_text)
        assert result == f"Note: {'a' * 30}..."

    def test_exactly_30_chars_not_truncated(self):
        text = "a" * 30
        result = _topic_name("note", text)
        assert result == f"Note: {'a' * 30}"

    def test_command_title_cased(self):
        assert _topic_name("ask", "what is life?") == "Ask: what is life?"


# ---------------------------------------------------------------------------
# help_text tests
# ---------------------------------------------------------------------------


class TestHelpText:
    def test_help_text_contains_all_commands(self):
        iface = _make_interface()
        for cmd in ["/note", "/ask", "/task", "/status", "/chat"]:
            assert cmd in iface.help_text

    def test_help_text_contains_descriptions(self):
        iface = _make_interface()
        assert "Capture a note" in iface.help_text
        assert "Query PKM" in iface.help_text

    def test_help_text_includes_status(self):
        iface = _make_interface()
        assert "/status - System dashboard" in iface.help_text


# ---------------------------------------------------------------------------
# dispatch_command early-return tests
# ---------------------------------------------------------------------------


class TestDispatchEarlyReturns:
    """Tests for dispatch_command paths that return before reaching session_manager."""

    @pytest.mark.asyncio
    async def test_unknown_command_sends_help(self):
        iface = _make_interface()
        with patch("telegram_interface.commands.send_message_return_id") as mock_send:
            await iface.dispatch_command("hello world")
            mock_send.assert_called_once()
            assert "Available commands" in mock_send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_note_without_text_sends_usage(self):
        iface = _make_interface()
        with patch("telegram_interface.commands.send_message_return_id") as mock_send:
            await iface.dispatch_command("/note")
            mock_send.assert_called_once()
            assert "Usage" in mock_send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_task_without_text_sends_usage(self):
        iface = _make_interface()
        with patch("telegram_interface.commands.send_message_return_id") as mock_send:
            await iface.dispatch_command("/task")
            mock_send.assert_called_once()
            assert "Usage" in mock_send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_ask_without_text_sends_usage(self):
        iface = _make_interface()
        with patch("telegram_interface.commands.send_message_return_id") as mock_send:
            await iface.dispatch_command("/ask")
            mock_send.assert_called_once()
            assert "Usage" in mock_send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_chat_without_text_sends_usage(self):
        iface = _make_interface()
        with patch("telegram_interface.commands.send_message_return_id") as mock_send:
            await iface.dispatch_command("/chat")
            mock_send.assert_called_once()
            assert "Usage" in mock_send.call_args[0][0]


# ---------------------------------------------------------------------------
# /status dashboard tests
# ---------------------------------------------------------------------------


class TestStatusDispatch:
    @pytest.mark.asyncio
    async def test_status_sends_dashboard(self):
        provider = AsyncMock(spec=StatusProvider)
        provider.get_status_sections.return_value = [
            StatusSection(title="Pipeline Status", entries=["Recordings: 5 total"]),
        ]
        iface = _make_interface(status_provider=provider)
        with patch("telegram_interface.commands.send_message_return_id") as mock_send:
            await iface.dispatch_command("/status")
            mock_send.assert_called_once()
            text = mock_send.call_args[0][0]
            assert "Pipeline Status" in text
            assert "Recordings: 5 total" in text

    @pytest.mark.asyncio
    async def test_status_no_provider(self):
        iface = _make_interface(status_provider=None)
        with patch("telegram_interface.commands.send_message_return_id") as mock_send:
            await iface.dispatch_command("/status")
            mock_send.assert_called_once()
            assert "No status providers configured" in mock_send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_status_provider_error_handled(self):
        provider = AsyncMock(spec=StatusProvider)
        provider.get_status_sections.side_effect = RuntimeError("db error")
        iface = _make_interface(status_provider=provider)
        with patch("telegram_interface.commands.send_message_return_id") as mock_send:
            await iface.dispatch_command("/status")
            mock_send.assert_called_once()
            assert "Error reading status data" in mock_send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_status_stays_in_general(self):
        """Status should not create a topic or use session_manager."""
        provider = AsyncMock(spec=StatusProvider)
        provider.get_status_sections.return_value = [
            StatusSection(title="Dashboard", entries=["All good"]),
        ]
        iface = _make_interface(status_provider=provider)
        with (
            patch("telegram_interface.commands.create_forum_topic") as mock_topic,
            patch("telegram_interface.commands.send_message_return_id") as mock_send,
        ):
            await iface.dispatch_command("/status")
            mock_topic.assert_not_called()
            iface._session_mgr.send.assert_not_called()
            mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# dispatch_command with topic + session tests
# ---------------------------------------------------------------------------


class TestDispatchCommandWithTopics:
    @pytest.mark.asyncio
    async def test_note_creates_topic_and_uses_session(self):
        iface = _make_interface()
        iface._session_mgr.send.return_value = SessionResponse(
            text="Saved to 01-Projects/notes.md",
            files_written=["01-Projects/notes.md"],
            session_id="sess-123",
        )

        with (
            patch("telegram_interface.commands.create_forum_topic", return_value=777) as mock_topic,
            patch("telegram_interface.commands.send_message_return_id", return_value=42) as mock_send,
        ):
            await iface.dispatch_command("/note buy milk")

            # Topic created with correct name
            mock_topic.assert_called_once_with("Note: buy milk", BOT)

            # Echo + ack + final response sent inside topic (3 calls)
            assert mock_send.call_count == 3
            echo_call, ack_call, response_call = mock_send.call_args_list
            assert echo_call[0][0] == "buy milk"
            assert echo_call[1]["thread_id"] == 777
            assert ack_call[0][0] == "Got it, routing..."
            assert ack_call[1]["thread_id"] == 777

            # session_manager.send used
            iface._session_mgr.send.assert_called_once()
            call_kwargs = iface._session_mgr.send.call_args
            assert call_kwargs[1]["session_key"] == "thread-777"

            # Final response includes file path
            assert "01-Projects/notes.md" in response_call[0][0]
            assert response_call[1]["thread_id"] == 777

    @pytest.mark.asyncio
    async def test_echo_not_sent_when_topic_creation_fails(self):
        iface = _make_interface()
        iface._session_mgr.send.return_value = SessionResponse(text="Saved")

        with (
            patch("telegram_interface.commands.create_forum_topic", return_value=None),
            patch("telegram_interface.commands.send_message_return_id", return_value=42) as mock_send,
        ):
            await iface.dispatch_command("/note buy milk")

            # Ack + final response (no echo since thread_id is None)
            assert mock_send.call_count == 2
            assert mock_send.call_args_list[0][0][0] == "Got it, routing..."

    @pytest.mark.asyncio
    async def test_echo_failure_does_not_block_processing(self):
        iface = _make_interface()
        iface._session_mgr.send.return_value = SessionResponse(text="Saved")

        call_count = 0

        async def mock_send(text, tg, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("echo failed")
            return 42

        with (
            patch("telegram_interface.commands.create_forum_topic", return_value=777),
            patch("telegram_interface.commands.send_message_return_id", side_effect=mock_send),
        ):
            await iface.dispatch_command("/note buy milk")

            # Agent still ran despite echo failure
            iface._session_mgr.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_topic_creation_failure_falls_back_to_general(self):
        iface = _make_interface()
        iface._session_mgr.send.return_value = SessionResponse(
            text="Saved to inbox",
            files_written=["00-Inbox/note.md"],
        )

        with (
            patch("telegram_interface.commands.create_forum_topic", return_value=None) as mock_topic,
            patch("telegram_interface.commands.send_message_return_id", return_value=42) as mock_send,
        ):
            await iface.dispatch_command("/note buy milk")

            # Topic creation was attempted
            mock_topic.assert_called_once()

            # Ack + final response sent without thread_id
            assert mock_send.call_count == 2
            assert mock_send.call_args_list[0][1].get("thread_id") is None

            # session_manager.send still used with general- key
            iface._session_mgr.send.assert_called_once()
            call_kwargs = iface._session_mgr.send.call_args
            assert call_kwargs[1]["session_key"] == "general-note"

    @pytest.mark.asyncio
    async def test_chat_creates_topic_with_thinking_ack(self):
        iface = _make_interface()
        iface._session_mgr.send.return_value = SessionResponse(
            text="Here's what I think about that.",
            files_written=[],
            session_id="sess-chat-1",
        )

        with (
            patch("telegram_interface.commands.create_forum_topic", return_value=888) as mock_topic,
            patch("telegram_interface.commands.send_message_return_id", return_value=42) as mock_send,
        ):
            await iface.dispatch_command("/chat what do you think about X?")

            # Topic created
            mock_topic.assert_called_once_with("Chat: what do you think about X?", BOT)

            # Echo + ack + final response (3 calls)
            assert mock_send.call_count == 3
            echo_call, ack_call, response_call = mock_send.call_args_list
            assert echo_call[0][0] == "what do you think about X?"
            assert ack_call[0][0] == "Thinking..."
            assert ack_call[1]["thread_id"] == 888

            # Final response
            assert "Here's what I think" in response_call[0][0]

    @pytest.mark.asyncio
    async def test_dispatch_uses_command_config_tools(self):
        """dispatch_command passes the CommandConfig's tools to session_manager.send."""
        iface = _make_interface()
        iface._session_mgr.send.return_value = SessionResponse(text="ok")

        with (
            patch("telegram_interface.commands.create_forum_topic", return_value=999),
            patch("telegram_interface.commands.send_message_return_id", return_value=42),
        ):
            await iface.dispatch_command("/ask what is life?")

            call_kwargs = iface._session_mgr.send.call_args[1]
            assert call_kwargs["allowed_tools"] == ["vault_read"]

    @pytest.mark.asyncio
    async def test_dispatch_uses_command_config_system_prompt(self):
        """dispatch_command passes the CommandConfig's system_prompt to session_manager.send."""
        iface = _make_interface()
        iface._session_mgr.send.return_value = SessionResponse(text="ok")

        with (
            patch("telegram_interface.commands.create_forum_topic", return_value=999),
            patch("telegram_interface.commands.send_message_return_id", return_value=42),
        ):
            await iface.dispatch_command("/chat hi")

            call_kwargs = iface._session_mgr.send.call_args[1]
            assert call_kwargs["system_prompt"] == "Chat prompt"


class TestInactivityTimeoutPlumbing:
    """Verify agent_inactivity_timeout_s set at construction time reaches
    SessionManager.send for both standalone /command dispatch and topic
    follow-ups. A regression here silently disables the watchdog at the
    daemon's configured timeout."""

    @pytest.mark.asyncio
    async def test_dispatch_command_forwards_inactivity_timeout(self):
        session_mgr = AsyncMock()
        session_mgr.send.return_value = SessionResponse(text="ok")
        thread_store = AsyncMock()
        iface = TelegramInterface(
            BOT, TEST_COMMANDS, session_mgr, thread_store,
            agent_inactivity_timeout_s=42.0,
        )

        with (
            patch("telegram_interface.commands.create_forum_topic", return_value=999),
            patch("telegram_interface.commands.send_message_return_id", return_value=1),
        ):
            await iface.dispatch_command("/ask anything")

        session_mgr.send.assert_called_once()
        assert session_mgr.send.call_args.kwargs["inactivity_timeout_s"] == 42.0

    @pytest.mark.asyncio
    async def test_handle_topic_message_forwards_inactivity_timeout(self):
        session_mgr = AsyncMock()
        session_mgr.send.return_value = SessionResponse(text="ok")
        thread_store = AsyncMock()
        thread_store.get_thread.return_value = ThreadRecord(
            thread_id=555, session_id="s1", name="Ask: foo", command="ask",
            created_at="2026-04-27T15:00:00",
        )
        iface = TelegramInterface(
            BOT, TEST_COMMANDS, session_mgr, thread_store,
            agent_inactivity_timeout_s=99.0,
        )

        with patch("telegram_interface.commands.send_message_return_id", return_value=1):
            await iface._handle_topic_message(555, "follow-up")

        session_mgr.send.assert_called_once()
        assert session_mgr.send.call_args.kwargs["inactivity_timeout_s"] == 99.0

    @pytest.mark.asyncio
    async def test_default_inactivity_timeout_is_none(self):
        """When no timeout is configured, send() is called with None — preserves
        the watchdog-disabled path so existing call sites don't change behaviour."""
        session_mgr = AsyncMock()
        session_mgr.send.return_value = SessionResponse(text="ok")
        thread_store = AsyncMock()
        iface = TelegramInterface(BOT, TEST_COMMANDS, session_mgr, thread_store)

        with (
            patch("telegram_interface.commands.create_forum_topic", return_value=999),
            patch("telegram_interface.commands.send_message_return_id", return_value=1),
        ):
            await iface.dispatch_command("/ask anything")

        assert session_mgr.send.call_args.kwargs["inactivity_timeout_s"] is None


# ---------------------------------------------------------------------------
# _format_response tests
# ---------------------------------------------------------------------------


class TestFormatResponse:
    def test_error_response(self):
        iface = _make_interface()
        resp = SessionResponse(text="bad", error="Client query failed")
        assert iface._format_response(resp) == "Failed: Client query failed"

    def test_files_written_response(self):
        iface = _make_interface()
        resp = SessionResponse(text="Saved", files_written=["01-Projects/note.md"])
        result = iface._format_response(resp)
        assert "Saved" in result
        assert "01-Projects/note.md" in result

    def test_plain_text_response(self):
        iface = _make_interface()
        resp = SessionResponse(text="The answer is 42.")
        assert iface._format_response(resp) == "The answer is 42."

    def test_truncation(self):
        iface = _make_interface()
        resp = SessionResponse(text="x" * 5000)
        result = iface._format_response(resp)
        assert len(result) == 4000
        assert result.endswith("...")

    def test_custom_file_formatter(self):
        def custom_fmt(files):
            return " | ".join(files)
        iface = _make_interface(file_formatter=custom_fmt)
        resp = SessionResponse(text="Done", files_written=["a.md", "b.md"])
        result = iface._format_response(resp)
        assert "a.md | b.md" in result


# ---------------------------------------------------------------------------
# _default_file_formatter tests
# ---------------------------------------------------------------------------


class TestDefaultFileFormatter:
    def test_single_file(self):
        assert _default_file_formatter(["foo.md"]) == "  foo.md"

    def test_multiple_files(self):
        result = _default_file_formatter(["a.md", "b.md"])
        assert result == "  a.md\n  b.md"


# ---------------------------------------------------------------------------
# Topic follow-up tests (ported from daemon's test_orchestrator.py)
# ---------------------------------------------------------------------------


class TestHandleTopicMessage:
    @pytest.mark.asyncio
    async def test_successful_topic_follow_up(self):
        """on_topic_message sends ack, calls session_mgr.send, edits with result."""
        iface = _make_interface()
        iface._thread_store.get_thread = AsyncMock(return_value=ThreadRecord(
            thread_id=42, session_id="sess-1", name="Ask: funding", command="ask", created_at="2026-01-01",
        ))
        iface._session_mgr.send = AsyncMock(return_value=SessionResponse(
            text="The answer is 42.", files_written=["01-Projects/note.md"], session_id="sess-2",
        ))

        with (
            patch("telegram_interface.commands.send_message_return_id", new_callable=AsyncMock, return_value=99) as mock_send,
            patch("telegram_interface.commands.edit_message_text", new_callable=AsyncMock) as mock_edit,
        ):
            await iface._handle_topic_message(42, "What about funding?")

        # Ack sent in topic
        mock_send.assert_called_once()
        assert mock_send.call_args[1]["thread_id"] == 42
        assert mock_send.call_args[0][0] == "Thinking..."

        # session_mgr.send called with correct args
        iface._session_mgr.send.assert_called_once()
        call_kw = iface._session_mgr.send.call_args[1]
        assert call_kw["session_key"] == "thread-42"

        # Result edited into ack
        mock_edit.assert_called_once()
        edited_text = mock_edit.call_args[0][2]
        assert "The answer is 42." in edited_text
        assert "01-Projects/note.md" in edited_text

    @pytest.mark.asyncio
    async def test_unknown_topic_ignored(self):
        """Messages in unknown topics are silently ignored."""
        iface = _make_interface()
        iface._thread_store.get_thread = AsyncMock(return_value=None)

        with patch("telegram_interface.commands.send_message_return_id", new_callable=AsyncMock) as mock_send:
            await iface._handle_topic_message(999, "hello?")

        # No ack or response sent
        mock_send.assert_not_called()
        iface._session_mgr.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_send_failure_sends_error_to_topic(self):
        """If session_mgr.send() raises, an error message is sent to the topic."""
        iface = _make_interface()
        iface._thread_store.get_thread = AsyncMock(return_value=ThreadRecord(
            thread_id=42, session_id="s", name="t", command="ask", created_at="2026-01-01",
        ))
        iface._session_mgr.send = AsyncMock(side_effect=RuntimeError("agent crashed"))

        with (
            patch("telegram_interface.commands.send_message_return_id", new_callable=AsyncMock, return_value=99) as mock_send,
            patch("telegram_interface.commands.edit_message_text", new_callable=AsyncMock) as mock_edit,
        ):
            # Should not raise
            await iface._handle_topic_message(42, "hello")

        # Error message edited into ack
        mock_edit.assert_called_once()
        assert "Failed to process" in mock_edit.call_args[0][2]

    @pytest.mark.asyncio
    async def test_error_response_shows_failure(self):
        """SessionResponse with error shows 'Failed:' prefix."""
        iface = _make_interface()
        iface._thread_store.get_thread = AsyncMock(return_value=ThreadRecord(
            thread_id=42, session_id="s", name="t", command="ask", created_at="2026-01-01",
        ))
        iface._session_mgr.send = AsyncMock(return_value=SessionResponse(
            text="Session error. Please try again.",
            error="Client query failed",
        ))

        with (
            patch("telegram_interface.commands.send_message_return_id", new_callable=AsyncMock, return_value=99),
            patch("telegram_interface.commands.edit_message_text", new_callable=AsyncMock) as mock_edit,
        ):
            await iface._handle_topic_message(42, "hello")

        edited_text = mock_edit.call_args[0][2]
        assert edited_text.startswith("Failed:")

    @pytest.mark.asyncio
    async def test_topic_follow_up_uses_command_system_prompt(self):
        """Follow-ups look up the command's system_prompt from CommandConfig."""
        iface = _make_interface()
        iface._thread_store.get_thread = AsyncMock(return_value=ThreadRecord(
            thread_id=42, session_id="s", name="t", command="note", created_at="2026-01-01",
        ))
        iface._session_mgr.send = AsyncMock(return_value=SessionResponse(text="ok"))

        with (
            patch("telegram_interface.commands.send_message_return_id", new_callable=AsyncMock, return_value=99),
            patch("telegram_interface.commands.edit_message_text", new_callable=AsyncMock),
        ):
            await iface._handle_topic_message(42, "more info")

        call_kw = iface._session_mgr.send.call_args[1]
        assert call_kw["system_prompt"] == "Note prompt"

    @pytest.mark.asyncio
    async def test_unknown_command_in_thread_uses_empty_prompt(self):
        """If the thread's command isn't in registered commands, use empty prompt."""
        iface = _make_interface()
        iface._thread_store.get_thread = AsyncMock(return_value=ThreadRecord(
            thread_id=42, session_id="s", name="t", command="deleted_cmd", created_at="2026-01-01",
        ))
        iface._session_mgr.send = AsyncMock(return_value=SessionResponse(text="ok"))

        with (
            patch("telegram_interface.commands.send_message_return_id", new_callable=AsyncMock, return_value=99),
            patch("telegram_interface.commands.edit_message_text", new_callable=AsyncMock),
        ):
            await iface._handle_topic_message(42, "follow up")

        call_kw = iface._session_mgr.send.call_args[1]
        assert call_kw["system_prompt"] == ""

    @pytest.mark.asyncio
    async def test_ack_send_failure_falls_back_to_new_message(self):
        """If editing the ack fails, a new message is sent instead."""
        iface = _make_interface()
        iface._thread_store.get_thread = AsyncMock(return_value=ThreadRecord(
            thread_id=42, session_id="s", name="t", command="ask", created_at="2026-01-01",
        ))
        iface._session_mgr.send = AsyncMock(return_value=SessionResponse(text="The answer"))

        with (
            patch("telegram_interface.commands.send_message_return_id", new_callable=AsyncMock, return_value=99) as mock_send,
            patch("telegram_interface.commands.edit_message_text", new_callable=AsyncMock, side_effect=Exception("edit failed")) as mock_edit,
        ):
            await iface._handle_topic_message(42, "question")

        # Edit was attempted
        mock_edit.assert_called_once()
        # Fallback: send as new message (ack + fallback = 2 calls)
        assert mock_send.call_count == 2

    @pytest.mark.asyncio
    async def test_no_ack_id_sends_new_message(self):
        """If the ack send fails (returns None), response is sent as new message."""
        iface = _make_interface()
        iface._thread_store.get_thread = AsyncMock(return_value=ThreadRecord(
            thread_id=42, session_id="s", name="t", command="ask", created_at="2026-01-01",
        ))
        iface._session_mgr.send = AsyncMock(return_value=SessionResponse(text="answer"))

        with (
            patch("telegram_interface.commands.send_message_return_id", new_callable=AsyncMock, return_value=None) as mock_send,
            patch("telegram_interface.commands.edit_message_text", new_callable=AsyncMock) as mock_edit,
        ):
            await iface._handle_topic_message(42, "question")

        # No edit (ack_id was None)
        mock_edit.assert_not_called()
        # Two sends: ack attempt + response
        assert mock_send.call_count == 2


# ---------------------------------------------------------------------------
# run_poller tests
# ---------------------------------------------------------------------------


class TestRunPoller:
    @pytest.mark.asyncio
    async def test_run_poller_passes_callbacks(self):
        iface = _make_interface()

        captured_kwargs = {}

        async def capture_poll(bot, labeling_reply=None, **kwargs):
            captured_kwargs.update(kwargs)

        with patch("telegram_interface.commands.poll_telegram_updates", side_effect=capture_poll):
            await iface.run_poller()

        assert "on_message" in captured_kwargs
        assert captured_kwargs["on_message"] is not None
        # Bound methods differ on each access; verify they point to the same underlying function
        assert captured_kwargs["on_message"].__func__ is TelegramInterface.dispatch_command
        assert "on_topic_message" in captured_kwargs
        assert captured_kwargs["on_topic_message"].__func__ is TelegramInterface._handle_topic_message

    @pytest.mark.asyncio
    async def test_run_poller_with_labeling_callback(self):
        iface = _make_interface()
        labeling_cb = AsyncMock()

        captured_args = []

        async def capture_poll(bot, labeling_reply=None, **kwargs):
            captured_args.append(labeling_reply)

        with patch("telegram_interface.commands.poll_telegram_updates", side_effect=capture_poll):
            await iface.run_poller(on_labeling_reply=labeling_cb)

        assert captured_args[0] is labeling_cb

    @pytest.mark.asyncio
    async def test_run_poller_with_callback_query(self):
        iface = _make_interface()
        callback_cb = AsyncMock()

        captured_kwargs = {}

        async def capture_poll(bot, labeling_reply=None, **kwargs):
            captured_kwargs.update(kwargs)

        with patch("telegram_interface.commands.poll_telegram_updates", side_effect=capture_poll):
            await iface.run_poller(on_callback_query=callback_cb)

        assert captured_kwargs["on_callback_query"] is callback_cb
