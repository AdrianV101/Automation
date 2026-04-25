import httpx
import pytest
from unittest.mock import AsyncMock, patch

from agent_infra import TraceEvent
from telegram_interface.trace import format_trace_event


class TestFormatTraceEvent:
    def test_tool_start_vault_read(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_read", tool_input={"path": "01-Projects/Automation/_index.md"})
        result = format_trace_event(e)
        assert "\U0001f4d6" in result  # book emoji
        assert "Reading:" in result
        assert "01-Projects/Automation/_index.md" in result

    def test_tool_start_vault_write(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_write", tool_input={"path": "00-Inbox/note.md", "template": "fleeting-note"})
        result = format_trace_event(e)
        assert "Writing:" in result
        assert "00-Inbox/note.md" in result

    def test_tool_start_vault_write_with_content(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_write", tool_input={
            "path": "00-Inbox/note.md",
            "content": "## Summary\n\nUpdated the trace feature.",
        })
        result = format_trace_event(e)
        assert "Writing:" in result
        assert "   <b>Summary</b>" in result
        assert "   Updated the trace feature." in result

    def test_tool_start_vault_write_strips_frontmatter(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_write", tool_input={
            "path": "00-Inbox/note.md",
            "content": "---\ntype: fleeting-note\ntags: [auto]\n---\n\n## The Real Content",
        })
        result = format_trace_event(e)
        assert "fleeting-note" not in result
        assert "   <b>The Real Content</b>" in result

    def test_tool_start_vault_write_truncates_long_content(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_write", tool_input={
            "path": "00-Inbox/note.md",
            "content": "x" * 600,
        })
        result = format_trace_event(e)
        assert "..." in result
        assert len(result) < 600

    def test_tool_start_vault_append(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_append", tool_input={"path": "devlog.md", "content": "## 2026-02-24\n- Updated streaming"})
        result = format_trace_event(e)
        assert "Appending:" in result
        assert "devlog.md" in result
        assert "   <b>2026-02-24</b>" in result
        assert "   - Updated streaming" in result

    def test_tool_start_vault_search(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_search", tool_input={"query": "project deadlines"})
        result = format_trace_event(e)
        assert "Searching:" in result
        assert "project deadlines" in result

    def test_tool_start_vault_semantic_search(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_semantic_search", tool_input={"query": "calendar integration"})
        result = format_trace_event(e)
        assert "Semantic search:" in result
        assert "calendar integration" in result

    def test_tool_start_vault_edit(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_edit", tool_input={
            "path": "note.md", "old_string": "old text", "new_string": "new replacement text",
        })
        result = format_trace_event(e)
        assert "Editing:" in result
        assert "note.md" in result
        assert "   new replacement text" in result

    def test_tool_start_vault_list(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_list", tool_input={"path": "01-Projects/"})
        result = format_trace_event(e)
        assert "Listing:" in result
        assert "01-Projects/" in result

    def test_tool_start_unknown_tool(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_tags", tool_input={})
        result = format_trace_event(e)
        assert "Vault Tags" in result

    def test_tool_start_no_input(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_recent", tool_input=None)
        result = format_trace_event(e)
        assert "Vault Recent" in result

    def test_tool_start_native_read(self):
        e = TraceEvent(kind="tool_start", tool_name="Read", tool_input={"file_path": "/home/user/src/main.py"})
        result = format_trace_event(e)
        assert "Read:" in result
        assert "/home/user/src/main.py" in result

    def test_tool_start_native_bash(self):
        e = TraceEvent(kind="tool_start", tool_name="Bash", tool_input={"command": "git status"})
        result = format_trace_event(e)
        assert "Bash:" in result
        assert "git status" in result

    def test_tool_start_native_task(self):
        e = TraceEvent(kind="tool_start", tool_name="Task", tool_input={"description": "Explore codebase structure"})
        result = format_trace_event(e)
        assert "Task:" in result
        assert "Explore codebase structure" in result

    def test_tool_start_native_edit(self):
        e = TraceEvent(kind="tool_start", tool_name="Edit", tool_input={"file_path": "/home/user/config.py", "old_string": "x", "new_string": "y"})
        result = format_trace_event(e)
        assert "Edit:" in result
        assert "/home/user/config.py" in result

    def test_tool_start_native_write(self):
        e = TraceEvent(kind="tool_start", tool_name="Write", tool_input={"file_path": "/home/user/new_file.py", "content": "print('hello')"})
        result = format_trace_event(e)
        assert "Write:" in result
        assert "/home/user/new_file.py" in result

    def test_tool_start_native_glob(self):
        e = TraceEvent(kind="tool_start", tool_name="Glob", tool_input={"pattern": "**/*.py"})
        result = format_trace_event(e)
        assert "Glob:" in result
        assert "**/*.py" in result

    def test_tool_start_native_grep(self):
        e = TraceEvent(kind="tool_start", tool_name="Grep", tool_input={"pattern": "def main"})
        result = format_trace_event(e)
        assert "Grep:" in result
        assert "def main" in result

    def test_tool_start_native_bash_truncates_long_command(self):
        long_cmd = "x" * 120
        e = TraceEvent(kind="tool_start", tool_name="Bash", tool_input={"command": long_cmd})
        result = format_trace_event(e)
        assert "..." in result
        assert len(result) < 120

    def test_tool_start_native_no_input(self):
        e = TraceEvent(kind="tool_start", tool_name="Read", tool_input={})
        result = format_trace_event(e)
        assert "Read" in result

    def test_tool_result(self):
        e = TraceEvent(kind="tool_result", content="## Phase 2\n- [x] Done\n- [ ] Pending")
        result = format_trace_event(e)
        assert "   " in result
        assert "Phase 2" in result

    def test_tool_result_empty(self):
        e = TraceEvent(kind="tool_result", content="")
        result = format_trace_event(e)
        assert "(empty)" in result

    def test_tool_result_none(self):
        e = TraceEvent(kind="tool_result", content=None)
        result = format_trace_event(e)
        assert "(empty)" in result

    def test_tool_result_truncated(self):
        e = TraceEvent(kind="tool_result", content="x" * 5000)
        result = format_trace_event(e)
        assert "... (truncated)" in result
        assert len(result) < 3000

    def test_text_event(self):
        e = TraceEvent(kind="text", content="Let me check the priorities.")
        result = format_trace_event(e)
        assert "\U0001f4ad" in result  # thought bubble
        assert "Let me check the priorities." in result

    def test_complete_event(self):
        e = TraceEvent(kind="complete", turns_used=5, cost_usd=0.12, files_written=["note.md"])
        result = format_trace_event(e)
        assert "\u2500" in result  # horizontal line
        assert "5 turns" in result
        assert "$0.12" in result

    def test_complete_event_no_cost(self):
        e = TraceEvent(kind="complete", turns_used=3, cost_usd=None)
        result = format_trace_event(e)
        assert "3 turns" in result

    def test_error_event(self):
        e = TraceEvent(kind="error", content="Agent SDK query failed")
        result = format_trace_event(e)
        assert "\u274c" in result
        assert "Agent SDK query failed" in result


class TestFormatTraceEventHtml:
    """Tests for HTML-safe output from format_trace_event."""

    def test_path_with_angle_brackets_escaped(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_read", tool_input={"path": "notes/<draft>.md"})
        result = format_trace_event(e)
        assert "&lt;draft&gt;" in result
        assert "<draft>" not in result

    def test_path_with_ampersand_escaped(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_read", tool_input={"path": "01-Projects/R&D/notes.md"})
        result = format_trace_event(e)
        assert "R&amp;D" in result

    def test_native_tool_path_escaped(self):
        e = TraceEvent(kind="tool_start", tool_name="Read", tool_input={"file_path": "/home/user/src/audio_ingest/config.py"})
        result = format_trace_event(e)
        # Underscores should pass through (not HTML special)
        assert "audio_ingest" in result

    def test_search_query_escaped(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_semantic_search", tool_input={"query": "find <urgent> tasks"})
        result = format_trace_event(e)
        assert "&lt;urgent&gt;" in result

    def test_write_preview_bold_converted_to_html(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_write", tool_input={
            "path": "00-Inbox/note.md",
            "content": "## Title\n\n**Priority:** High",
        })
        result = format_trace_event(e)
        assert "<b>Title</b>" in result
        assert "<b>Priority:</b>" in result

    def test_write_preview_html_chars_in_content_escaped(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_write", tool_input={
            "path": "note.md",
            "content": "Use if x < 10 && y > 5",
        })
        result = format_trace_event(e)
        assert "&lt;" in result
        assert "&gt;" in result
        assert "&amp;" in result

    def test_write_preview_bold_with_html_chars(self):
        e = TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_write", tool_input={
            "path": "note.md",
            "content": "**R&D Update**",
        })
        result = format_trace_event(e)
        assert "<b>R&amp;D Update</b>" in result

    def test_error_with_html_chars_escaped(self):
        e = TraceEvent(kind="error", content="ValueError at <module> line 42")
        result = format_trace_event(e)
        assert "&lt;module&gt;" in result
        assert "<module>" not in result


from telegram_interface.types import BotConfig
from telegram_interface.trace import TelegramStreamSender


def _tg_config() -> BotConfig:
    return BotConfig(bot_token="fake-token", chat_id="12345")


class TestTelegramStreamSender:
    @pytest.mark.asyncio
    async def test_first_event_creates_message(self):
        tg = _tg_config()
        sender = TelegramStreamSender(tg, thread_id=100)

        with patch("telegram_interface.trace.send_message_return_id", new_callable=AsyncMock, return_value=999) as mock_send:
            await sender.handle(TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_read", tool_input={"path": "note.md"}))
            await sender.flush()

        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert "Reading" in call_args[0][0]
        assert call_args[1]["thread_id"] == 100

    @pytest.mark.asyncio
    async def test_same_category_edits_message(self):
        """Multiple tool_start events of the same category edit the same message."""
        tg = _tg_config()
        sender = TelegramStreamSender(tg, thread_id=100)

        with (
            patch("telegram_interface.trace.send_message_return_id", new_callable=AsyncMock, return_value=999),
            patch("telegram_interface.trace.edit_message_text", new_callable=AsyncMock) as mock_edit,
        ):
            await sender.handle(TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_read", tool_input={"path": "a.md"}))
            await sender.flush()
            await sender.handle(TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_read", tool_input={"path": "b.md"}))
            await sender.flush()

        assert mock_edit.call_count >= 1
        last_call = mock_edit.call_args
        assert "a.md" in last_call[0][2]
        assert "b.md" in last_call[0][2]

    @pytest.mark.asyncio
    async def test_category_change_creates_new_message(self):
        """Switching from read to write creates a new message."""
        tg = _tg_config()
        sender = TelegramStreamSender(tg, thread_id=100)

        with (
            patch("telegram_interface.trace.send_message_return_id", new_callable=AsyncMock, return_value=999) as mock_send,
            patch("telegram_interface.trace.edit_message_text", new_callable=AsyncMock),
        ):
            await sender.handle(TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_read", tool_input={"path": "a.md"}))
            await sender.flush()
            await sender.handle(TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_write", tool_input={"path": "b.md"}))
            await sender.flush()

        # Two separate messages: one for read, one for write
        assert mock_send.call_count == 2

    @pytest.mark.asyncio
    async def test_tool_result_skipped(self):
        """tool_result events are silently skipped."""
        tg = _tg_config()
        sender = TelegramStreamSender(tg, thread_id=100)

        with patch("telegram_interface.trace.send_message_return_id", new_callable=AsyncMock, return_value=999) as mock_send:
            await sender.handle(TraceEvent(kind="tool_result", content="file contents"))
            await sender.flush()

        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_text_event_skipped(self):
        """Text events are silently skipped (final response shows text)."""
        tg = _tg_config()
        sender = TelegramStreamSender(tg, thread_id=100)

        with patch("telegram_interface.trace.send_message_return_id", new_callable=AsyncMock, return_value=999) as mock_send:
            await sender.handle(TraceEvent(kind="text", content="Summary"))
            await sender.flush()

        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_complete_flushes_with_turn_count_no_cost(self):
        """Complete event flushes pending tool trace with turn count but no cost."""
        tg = _tg_config()
        sender = TelegramStreamSender(tg, thread_id=100)

        with (
            patch("telegram_interface.trace.send_message_return_id", new_callable=AsyncMock, return_value=999) as mock_send,
            patch("telegram_interface.trace.edit_message_text", new_callable=AsyncMock) as mock_edit,
        ):
            await sender.handle(TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_write", tool_input={"path": "note.md"}))
            await sender.handle(TraceEvent(kind="complete", turns_used=3, cost_usd=0.05))

        # Initial send has the tool trace
        assert mock_send.call_count == 1
        # Edit appends the turn count footer
        assert mock_edit.call_count == 1
        edited_text = mock_edit.call_args[0][2]
        assert "Writing" in edited_text
        assert "3 turns" in edited_text
        assert "$" not in edited_text

    @pytest.mark.asyncio
    async def test_no_thread_id(self):
        tg = _tg_config()
        sender = TelegramStreamSender(tg)

        with patch("telegram_interface.trace.send_message_return_id", new_callable=AsyncMock, return_value=999) as mock_send:
            await sender.handle(TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_read", tool_input={"path": "note.md"}))
            await sender.flush()

        call_kwargs = mock_send.call_args[1]
        assert call_kwargs.get("thread_id") is None

    @pytest.mark.asyncio
    async def test_edit_failure_sends_new_message(self):
        tg = _tg_config()
        sender = TelegramStreamSender(tg, thread_id=100)

        with (
            patch("telegram_interface.trace.send_message_return_id", new_callable=AsyncMock, return_value=999) as mock_send,
            patch("telegram_interface.trace.edit_message_text", new_callable=AsyncMock, side_effect=Exception("edit failed")),
        ):
            # Two same-category reads — second triggers an edit which fails
            await sender.handle(TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_read", tool_input={"path": "a.md"}))
            await sender.flush()
            await sender.handle(TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_read", tool_input={"path": "b.md"}))
            await sender.flush()

        assert mock_send.call_count >= 2

    @pytest.mark.asyncio
    async def test_rate_limiting(self):
        """Same-category events are rate-limited within a message."""
        tg = _tg_config()
        sender = TelegramStreamSender(tg, thread_id=100, flush_interval=0.5)

        with (
            patch("telegram_interface.trace.send_message_return_id", new_callable=AsyncMock, return_value=999) as mock_send,
            patch("telegram_interface.trace.edit_message_text", new_callable=AsyncMock) as mock_edit,
        ):
            # All same category (read) — should stay in one message
            for i in range(5):
                await sender.handle(TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_read", tool_input={"path": f"file{i}.md"}))

            assert mock_send.call_count <= 1
            assert mock_edit.call_count == 0

            await sender.flush()

    @pytest.mark.asyncio
    async def test_sent_text_not_advanced_on_failed_send(self):
        """_sent_text should not advance when send_message_return_id returns None."""
        tg = _tg_config()
        sender = TelegramStreamSender(tg, thread_id=100)

        with patch("telegram_interface.trace.send_message_return_id", new_callable=AsyncMock, return_value=None):
            await sender.handle(TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_read", tool_input={"path": "test.md"}))
            await sender.flush()

        assert sender._sent_text == ""
        assert sender._current_msg_id is None

    @pytest.mark.asyncio
    async def test_flush_sends_with_html_parse_mode(self):
        """flush() should send messages with parse_mode='HTML'."""
        tg = _tg_config()
        sender = TelegramStreamSender(tg, thread_id=100)

        with patch("telegram_interface.trace.send_message_return_id", new_callable=AsyncMock, return_value=999) as mock_send:
            await sender.handle(TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_read", tool_input={"path": "note.md"}))
            await sender.flush()

        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_flush_edit_uses_html_parse_mode(self):
        """flush() should edit messages with parse_mode='HTML'."""
        tg = _tg_config()
        sender = TelegramStreamSender(tg, thread_id=100)

        with (
            patch("telegram_interface.trace.send_message_return_id", new_callable=AsyncMock, return_value=999),
            patch("telegram_interface.trace.edit_message_text", new_callable=AsyncMock) as mock_edit,
        ):
            await sender.handle(TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_read", tool_input={"path": "a.md"}))
            await sender.flush()
            await sender.handle(TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_read", tool_input={"path": "b.md"}))
            await sender.flush()

        call_kwargs = mock_edit.call_args[1]
        assert call_kwargs.get("parse_mode") == "HTML"

    @pytest.mark.asyncio
    async def test_flush_falls_back_to_plain_text_on_400(self):
        """If HTML send fails with 400, retry without parse_mode."""
        tg = _tg_config()
        sender = TelegramStreamSender(tg, thread_id=100)

        response_400 = httpx.Response(400, request=httpx.Request("POST", "http://test"))
        html_error = httpx.HTTPStatusError("Bad Request", request=response_400.request, response=response_400)

        call_count = 0

        async def send_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise html_error
            return 999

        with patch("telegram_interface.trace.send_message_return_id", new_callable=AsyncMock, side_effect=send_side_effect) as mock_send:
            await sender.handle(TraceEvent(kind="tool_start", tool_name="mcp__obsidian-pkm__vault_read", tool_input={"path": "note.md"}))
            await sender.flush()

        assert mock_send.call_count == 2
        # First call: with HTML
        assert mock_send.call_args_list[0][1]["parse_mode"] == "HTML"
        # Second call: plain text fallback (no parse_mode)
        assert mock_send.call_args_list[1][1].get("parse_mode") is None
