from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from telegram_interface.types import BotConfig
from telegram_interface.bot import (
    edit_message_text,
    escape_html,
    markdown_to_telegram_html,
    poll_telegram_updates,
    send_message,
    send_message_return_id,
)


@pytest.fixture
def tg_config():
    return BotConfig(bot_token="test_bot_token", chat_id="12345")


class TestSendMessage:
    @respx.mock
    @pytest.mark.asyncio
    async def test_sends_text(self, tg_config):
        route = respx.post(
            f"https://api.telegram.org/bot{tg_config.bot_token}/sendMessage"
        ).mock(return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}))

        await send_message("hello", tg_config)

        assert route.called
        body = route.calls[0].request.content.decode()
        assert "hello" in body


class TestSendMessageReturnId:
    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_message_id(self, tg_config):
        route = respx.post(
            f"https://api.telegram.org/bot{tg_config.bot_token}/sendMessage"
        ).mock(
            return_value=httpx.Response(
                200, json={"ok": True, "result": {"message_id": 42}},
            ),
        )

        result = await send_message_return_id("hello", tg_config)

        assert result == 42
        assert route.called

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_none_on_missing_id(self, tg_config):
        respx.post(
            f"https://api.telegram.org/bot{tg_config.bot_token}/sendMessage"
        ).mock(
            return_value=httpx.Response(200, json={"ok": True, "result": {}}),
        )

        result = await send_message_return_id("hello", tg_config)

        assert result is None


class TestEditMessageText:
    @respx.mock
    @pytest.mark.asyncio
    async def test_calls_edit_endpoint(self, tg_config):
        route = respx.post(
            f"https://api.telegram.org/bot{tg_config.bot_token}/editMessageText"
        ).mock(return_value=httpx.Response(200, json={"ok": True}))

        await edit_message_text(tg_config.chat_id, 42, "updated", tg_config)

        assert route.called
        import json

        payload = json.loads(route.calls[0].request.content)
        assert payload["chat_id"] == tg_config.chat_id
        assert payload["message_id"] == 42
        assert payload["text"] == "updated"


def _make_update(update_id, chat_id, text, reply_to_message_id=None):
    """Helper to build a Telegram update dict."""
    message = {
        "message_id": update_id * 10,
        "chat": {"id": int(chat_id)},
        "text": text,
    }
    if reply_to_message_id is not None:
        message["reply_to_message"] = {"message_id": reply_to_message_id}
    return {"update_id": update_id, "message": message}


# Use a 401 response as the second call to break the polling loop cleanly.
# poll_telegram_updates catches TimeoutException internally (continue), but
# returns on 401/403 HTTPStatusError, so this reliably stops the loop.
_STOP_POLLING = httpx.Response(401, json={"ok": False})


class TestPollStandaloneMessages:
    """Tests for on_message callback in poll_telegram_updates."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_standalone_message_dispatched(self, tg_config):
        """Non-reply messages with text should be dispatched to on_message."""
        update = _make_update(1, tg_config.chat_id, "hello bot")
        respx.get(
            f"https://api.telegram.org/bot{tg_config.bot_token}/getUpdates"
        ).mock(
            side_effect=[
                httpx.Response(200, json={"ok": True, "result": [update]}),
                _STOP_POLLING,
            ]
        )

        on_labeling_reply = AsyncMock()
        on_message = AsyncMock()

        await poll_telegram_updates(
            tg_config, on_labeling_reply, on_message=on_message,
        )

        on_message.assert_awaited_once_with("hello bot")
        on_labeling_reply.assert_not_awaited()

    @respx.mock
    @pytest.mark.asyncio
    async def test_replies_still_go_to_on_labeling_reply(self, tg_config):
        """Reply messages should still go to on_labeling_reply, not on_message."""
        update = _make_update(
            1, tg_config.chat_id, "Speaker A", reply_to_message_id=99,
        )
        respx.get(
            f"https://api.telegram.org/bot{tg_config.bot_token}/getUpdates"
        ).mock(
            side_effect=[
                httpx.Response(200, json={"ok": True, "result": [update]}),
                _STOP_POLLING,
            ]
        )

        on_labeling_reply = AsyncMock()
        on_message = AsyncMock()

        await poll_telegram_updates(
            tg_config, on_labeling_reply, on_message=on_message,
        )

        on_labeling_reply.assert_awaited_once_with(99, "Speaker A")
        on_message.assert_not_awaited()

    @respx.mock
    @pytest.mark.asyncio
    async def test_on_message_optional_backward_compat(self, tg_config):
        """When on_message is not provided, standalone messages are silently dropped."""
        update = _make_update(1, tg_config.chat_id, "hello bot")
        respx.get(
            f"https://api.telegram.org/bot{tg_config.bot_token}/getUpdates"
        ).mock(
            side_effect=[
                httpx.Response(200, json={"ok": True, "result": [update]}),
                _STOP_POLLING,
            ]
        )

        on_labeling_reply = AsyncMock()

        # Should not raise -- on_message defaults to None, standalone silently dropped
        await poll_telegram_updates(tg_config, on_labeling_reply)

        on_labeling_reply.assert_not_awaited()

    @respx.mock
    @pytest.mark.asyncio
    async def test_ignores_standalone_from_wrong_chat(self, tg_config):
        """Standalone messages from other chats should be ignored."""
        update = _make_update(1, "99999", "hello bot")  # wrong chat ID
        respx.get(
            f"https://api.telegram.org/bot{tg_config.bot_token}/getUpdates"
        ).mock(
            side_effect=[
                httpx.Response(200, json={"ok": True, "result": [update]}),
                _STOP_POLLING,
            ]
        )

        on_labeling_reply = AsyncMock()
        on_message = AsyncMock()

        await poll_telegram_updates(
            tg_config, on_labeling_reply, on_message=on_message,
        )

        on_message.assert_not_awaited()
        on_labeling_reply.assert_not_awaited()


class TestEscapeHtml:
    def test_escapes_angle_brackets(self):
        assert escape_html("<b>not bold</b>") == "&lt;b&gt;not bold&lt;/b&gt;"

    def test_escapes_ampersand(self):
        assert escape_html("R&D") == "R&amp;D"

    def test_leaves_safe_chars_alone(self):
        assert escape_html("01-Projects/Automation/_index.md") == "01-Projects/Automation/_index.md"

    def test_handles_all_three(self):
        assert escape_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"

    def test_empty_string(self):
        assert escape_html("") == ""

    def test_underscores_and_asterisks_safe(self):
        """Underscores and asterisks are NOT html special chars."""
        text = "setup_calendar.md **bold** *italic*"
        assert escape_html(text) == text


class TestMarkdownToTelegramHtml:
    def test_converts_double_asterisk_bold(self):
        assert markdown_to_telegram_html("**bold**") == "<b>bold</b>"

    def test_converts_heading(self):
        assert markdown_to_telegram_html("## Heading") == "<b>Heading</b>"

    def test_converts_h1_through_h6(self):
        for i in range(1, 7):
            hashes = "#" * i
            assert markdown_to_telegram_html(f"{hashes} Title") == "<b>Title</b>"

    def test_heading_must_be_at_line_start(self):
        assert markdown_to_telegram_html("   ## indented") == "   ## indented"

    def test_escapes_html_before_converting(self):
        result = markdown_to_telegram_html("**foo <bar> baz**")
        assert result == "<b>foo &lt;bar&gt; baz</b>"

    def test_ampersand_in_plain_text(self):
        result = markdown_to_telegram_html("R&D project")
        assert result == "R&amp;D project"

    def test_unmatched_bold_stays_literal(self):
        """Truncation mid-bold should not break anything."""
        result = markdown_to_telegram_html("**truncated text")
        assert result == "**truncated text"

    def test_multiple_bold_segments(self):
        result = markdown_to_telegram_html("**a** and **b**")
        assert result == "<b>a</b> and <b>b</b>"

    def test_mixed_heading_and_bold(self):
        result = markdown_to_telegram_html("## Title\n\n**Priority:** High")
        assert result == "<b>Title</b>\n\n<b>Priority:</b> High"

    def test_bullet_points_safe(self):
        """Single * should not break anything."""
        result = markdown_to_telegram_html("* item one\n* item two")
        assert result == "* item one\n* item two"

    def test_path_with_underscores_safe(self):
        result = markdown_to_telegram_html("setup_calendar.md")
        assert result == "setup_calendar.md"
