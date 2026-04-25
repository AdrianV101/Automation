"""Integration tests for streaming agent trace pipeline.

Tests the full flow: mock Agent SDK query() -> run_agent_loop_streaming ->
TraceEvent emission -> TelegramStreamSender -> Telegram API calls.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agent_infra import TraceEvent, build_agent_options, run_agent_loop_streaming
from telegram_interface import TelegramStreamSender, format_trace_event
from telegram_interface.types import BotConfig

from conftest import (
    MockAssistantMessage,
    MockTextBlock,
    MockToolUseBlock,
    MockUserMessage,
    MockToolResultBlock,
    MockResultMessage,
    make_assistant_message,
    make_user_message,
    make_result_message,
)


def _tg_config() -> BotConfig:
    return BotConfig(bot_token="fake-token", chat_id="12345")


class TestStreamingEndToEnd:
    """Test the full pipeline: query -> events -> formatter -> Telegram."""

    @pytest.mark.asyncio
    async def test_full_agent_session_produces_trace(self, tmp_path):
        """A realistic multi-turn agent session produces correct trace messages."""
        # Simulate: search -> read -> write -> text response -> result
        search_msg = make_assistant_message(
            tool_blocks=[("mcp__obsidian-pkm__vault_search", {"query": "project deadlines"})],
        )
        search_result = make_user_message(
            tool_results=[("t1", "_index.md, priorities.md")],
        )
        read_msg = make_assistant_message(
            tool_blocks=[("mcp__obsidian-pkm__vault_read", {"path": "01-Projects/Automation/_index.md"})],
        )
        read_result = make_user_message(
            tool_results=[("t2", "## Phase 2: Composability\n- [x] Done")],
        )
        write_msg = make_assistant_message(
            tool_blocks=[("mcp__obsidian-pkm__vault_write", {"path": "00-Inbox/note.md", "template": "fleeting-note"})],
        )
        write_result = make_user_message(
            tool_results=[("t3", "Created 00-Inbox/note.md")],
        )
        final_msg = make_assistant_message(text_blocks=["I've stored the note in your inbox."])
        result_msg = make_result_message(num_turns=4, total_cost_usd=0.15)

        events: list[TraceEvent] = []

        async def mock_query(prompt, options):
            for msg in [search_msg, search_result, read_msg, read_result, write_msg, write_result, final_msg, result_msg]:
                yield msg

        async def capture(event: TraceEvent):
            events.append(event)

        with (
            patch("agent_infra.runner.query", side_effect=mock_query),
            patch("agent_infra.runner.AssistantMessage", MockAssistantMessage),
            patch("agent_infra.runner.TextBlock", MockTextBlock),
            patch("agent_infra.runner.ToolUseBlock", MockToolUseBlock),
            patch("agent_infra.runner.UserMessage", MockUserMessage),
            patch("agent_infra.runner.ToolResultBlock", MockToolResultBlock),
            patch("agent_infra.runner.ResultMessage", MockResultMessage),
        ):
            opts = build_agent_options("system prompt", tmp_path)
            result = await run_agent_loop_streaming("test prompt", opts, on_event=capture)

        # Verify event sequence
        kinds = [e.kind for e in events]
        assert kinds == [
            "tool_start",    # search
            "tool_result",   # search result
            "tool_start",    # read
            "tool_result",   # read result
            "tool_start",    # write
            "tool_result",   # write result
            "text",          # final response
            "complete",      # result
        ]

        # Verify tool names
        assert events[0].tool_name == "mcp__obsidian-pkm__vault_search"
        assert events[2].tool_name == "mcp__obsidian-pkm__vault_read"
        assert events[4].tool_name == "mcp__obsidian-pkm__vault_write"

        # Verify result
        assert result.files_written == ["00-Inbox/note.md"]
        assert result.text_parts == ["I've stored the note in your inbox."]

    @pytest.mark.asyncio
    async def test_events_format_correctly(self):
        """Each event kind produces the expected emoji prefix."""
        events = [
            TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_search", tool_input={"query": "test"}),
            TraceEvent(kind="tool_result", content="Found 3 results"),
            TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_read", tool_input={"path": "note.md"}),
            TraceEvent(kind="tool_result", content="# Note\nContent here"),
            TraceEvent(kind="text", content="Based on what I found..."),
            TraceEvent(kind="complete", turns_used=2, cost_usd=0.05),
        ]

        formatted = [format_trace_event(e) for e in events]

        assert "\U0001f50d" in formatted[0]  # search emoji
        assert "Found 3 results" in formatted[1]
        assert "\U0001f4d6" in formatted[2]  # book emoji
        assert "Content here" in formatted[3]
        assert "\U0001f4ad" in formatted[4]  # thought bubble
        assert "2 turns" in formatted[5]
        assert "$0.05" in formatted[5]

    @pytest.mark.asyncio
    async def test_sender_creates_and_edits_for_same_category(self, tmp_path):
        """Same-category tool calls edit the same message; different category creates new."""
        tg = _tg_config()
        sender = TelegramStreamSender(tg, thread_id=42)

        with (
            patch("telegram_interface.trace.send_message_return_id", new_callable=AsyncMock, return_value=100) as mock_send,
            patch("telegram_interface.trace.edit_message_text", new_callable=AsyncMock) as mock_edit,
        ):
            # Two reads (same category) — second should edit
            await sender.handle(TraceEvent(
                kind="tool_start",
                tool_name="mcp__obsidian-pkm__vault_read",
                tool_input={"path": "note1.md"},
            ))
            await sender.flush()
            await sender.handle(TraceEvent(
                kind="tool_start",
                tool_name="mcp__obsidian-pkm__vault_read",
                tool_input={"path": "note2.md"},
            ))
            await sender.flush()

            # Switch to write (different category) — new message
            await sender.handle(TraceEvent(
                kind="tool_start",
                tool_name="mcp__obsidian-pkm__vault_write",
                tool_input={"path": "output.md"},
            ))
            await sender.flush()

        # Message 1: read (created), then edited with second read
        # Message 2: write (created)
        assert mock_send.call_count == 2
        first_send = mock_send.call_args_list[0]
        assert "Reading" in first_send[0][0]
        assert first_send[1]["thread_id"] == 42
        assert mock_edit.call_count >= 1

    @pytest.mark.asyncio
    async def test_text_skipped_complete_not_sent(self, tmp_path):
        """Text events are skipped; complete flushes pending but sends no footer."""
        tg = _tg_config()
        sender = TelegramStreamSender(tg, thread_id=42)

        with (
            patch("telegram_interface.trace.send_message_return_id", new_callable=AsyncMock, return_value=100) as mock_send,
            patch("telegram_interface.trace.edit_message_text", new_callable=AsyncMock),
        ):
            await sender.handle(TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_write", tool_input={"path": "note.md"}))
            await sender.flush()
            await sender.handle(TraceEvent(kind="text", content="Done processing"))
            await sender.flush()
            await sender.handle(TraceEvent(kind="complete", turns_used=3, cost_usd=0.05))

        # 1 message: tool only (text skipped, complete flushes but sends no footer)
        assert mock_send.call_count == 1
        sent_text = mock_send.call_args[0][0]
        assert "Writing" in sent_text
        assert "turns" not in sent_text

    @pytest.mark.asyncio
    async def test_error_event_displayed(self, tmp_path):
        """Error events are formatted and sent to Telegram."""
        tg = _tg_config()
        sender = TelegramStreamSender(tg, thread_id=42)

        with patch("telegram_interface.trace.send_message_return_id", new_callable=AsyncMock, return_value=100) as mock_send:
            await sender.handle(TraceEvent(kind="error", content="Connection lost"))

        assert mock_send.call_count == 1
        sent_text = mock_send.call_args[0][0]
        assert "\u274c" in sent_text
        assert "Connection lost" in sent_text
