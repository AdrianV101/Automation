from __future__ import annotations

import json

import httpx
import pytest
import respx

from telegram_interface.types import BotConfig
from telegram_interface.bot import (
    check_topics_enabled,
    close_forum_topic,
    create_forum_topic,
    edit_message_text,
    reopen_forum_topic,
    send_message,
    send_message_return_id,
)

_TG = BotConfig(bot_token="test-token", chat_id="123")
_API = "https://api.telegram.org/bottest-token"


class TestCreateForumTopic:
    @respx.mock
    @pytest.mark.asyncio
    async def test_creates_topic_and_returns_thread_id(self):
        route = respx.post(f"{_API}/createForumTopic").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {"message_thread_id": 42, "name": "Ask: funding"},
                },
            ),
        )

        thread_id = await create_forum_topic("Ask: funding", _TG)

        assert thread_id == 42
        assert route.called
        payload = json.loads(route.calls[0].request.content)
        assert payload["name"] == "Ask: funding"
        assert payload["chat_id"] == "123"

    @respx.mock
    @pytest.mark.asyncio
    async def test_truncates_long_names(self):
        long_name = "x" * 200
        route = respx.post(f"{_API}/createForumTopic").mock(
            return_value=httpx.Response(
                200,
                json={"ok": True, "result": {"message_thread_id": 7}},
            ),
        )

        await create_forum_topic(long_name, _TG)

        payload = json.loads(route.calls[0].request.content)
        assert len(payload["name"]) == 128

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self):
        respx.post(f"{_API}/createForumTopic").mock(
            return_value=httpx.Response(500, json={"ok": False}),
        )

        thread_id = await create_forum_topic("Test", _TG)

        assert thread_id is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self):
        respx.post(f"{_API}/createForumTopic").mock(
            side_effect=httpx.ConnectError("connection refused"),
        )

        thread_id = await create_forum_topic("Test", _TG)

        assert thread_id is None


class TestCloseForumTopic:
    @respx.mock
    @pytest.mark.asyncio
    async def test_calls_close_endpoint(self):
        route = respx.post(f"{_API}/closeForumTopic").mock(
            return_value=httpx.Response(200, json={"ok": True}),
        )

        await close_forum_topic(42, _TG)

        assert route.called
        payload = json.loads(route.calls[0].request.content)
        assert payload["message_thread_id"] == 42
        assert payload["chat_id"] == "123"

    @respx.mock
    @pytest.mark.asyncio
    async def test_does_not_raise_on_http_error(self):
        respx.post(f"{_API}/closeForumTopic").mock(
            return_value=httpx.Response(500, json={"ok": False}),
        )

        # Should not raise -- error is logged internally
        await close_forum_topic(42, _TG)

    @respx.mock
    @pytest.mark.asyncio
    async def test_does_not_raise_on_network_error(self):
        respx.post(f"{_API}/closeForumTopic").mock(
            side_effect=httpx.ConnectError("connection refused"),
        )

        # Should not raise -- error is logged internally
        await close_forum_topic(42, _TG)


class TestReopenForumTopic:
    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_true_on_success(self):
        route = respx.post(f"{_API}/reopenForumTopic").mock(
            return_value=httpx.Response(200, json={"ok": True}),
        )

        result = await reopen_forum_topic(42, _TG)

        assert result is True
        assert route.called
        payload = json.loads(route.calls[0].request.content)
        assert payload["message_thread_id"] == 42

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_true_when_already_open(self):
        """TOPIC_NOT_MODIFIED means topic is already open -- should return True."""
        respx.post(f"{_API}/reopenForumTopic").mock(
            return_value=httpx.Response(
                400,
                json={"ok": False, "description": "Bad Request: TOPIC_NOT_MODIFIED"},
            ),
        )

        result = await reopen_forum_topic(42, _TG)

        assert result is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_false_on_other_400_error(self):
        respx.post(f"{_API}/reopenForumTopic").mock(
            return_value=httpx.Response(
                400,
                json={"ok": False, "description": "Bad Request: TOPIC_CLOSED"},
            ),
        )

        result = await reopen_forum_topic(42, _TG)

        assert result is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_false_on_network_error(self):
        respx.post(f"{_API}/reopenForumTopic").mock(
            side_effect=httpx.ConnectError("connection refused"),
        )

        result = await reopen_forum_topic(42, _TG)

        assert result is False


class TestThreadIdParam:
    """Verify thread_id kwarg on send_message_return_id, send_message, edit_message_text."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_send_message_return_id_includes_thread_id(self):
        route = respx.post(f"{_API}/sendMessage").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "result": {"message_id": 99}},
            ),
        )

        msg_id = await send_message_return_id("hello", _TG, thread_id=42)

        assert msg_id == 99
        payload = json.loads(route.calls[0].request.content)
        assert payload["message_thread_id"] == 42

    @respx.mock
    @pytest.mark.asyncio
    async def test_send_message_return_id_omits_thread_id_when_none(self):
        route = respx.post(f"{_API}/sendMessage").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "result": {"message_id": 1}},
            ),
        )

        await send_message_return_id("hello", _TG)

        payload = json.loads(route.calls[0].request.content)
        assert "message_thread_id" not in payload

    @respx.mock
    @pytest.mark.asyncio
    async def test_send_message_passes_thread_id_through(self):
        route = respx.post(f"{_API}/sendMessage").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "result": {"message_id": 1}},
            ),
        )

        await send_message("hi", _TG, thread_id=7)

        payload = json.loads(route.calls[0].request.content)
        assert payload["message_thread_id"] == 7

    @respx.mock
    @pytest.mark.asyncio
    async def test_edit_message_text_includes_thread_id(self):
        route = respx.post(f"{_API}/editMessageText").mock(
            return_value=httpx.Response(200, json={"ok": True}),
        )

        await edit_message_text("123", 55, "updated", _TG, thread_id=10)

        payload = json.loads(route.calls[0].request.content)
        assert payload["message_thread_id"] == 10
        assert payload["message_id"] == 55
        assert payload["text"] == "updated"

    @respx.mock
    @pytest.mark.asyncio
    async def test_edit_message_text_omits_thread_id_when_none(self):
        route = respx.post(f"{_API}/editMessageText").mock(
            return_value=httpx.Response(200, json={"ok": True}),
        )

        await edit_message_text("123", 55, "updated", _TG)

        payload = json.loads(route.calls[0].request.content)
        assert "message_thread_id" not in payload


class TestCheckTopicsEnabled:
    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_true_when_enabled(self):
        respx.get(f"{_API}/getChat").mock(
            return_value=httpx.Response(
                200,
                json={"ok": True, "result": {"id": 123, "is_forum": True}},
            ),
        )

        result = await check_topics_enabled(_TG)

        assert result is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_false_when_disabled(self):
        respx.get(f"{_API}/getChat").mock(
            return_value=httpx.Response(
                200,
                json={"ok": True, "result": {"id": 123, "is_forum": False}},
            ),
        )

        result = await check_topics_enabled(_TG)

        assert result is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_false_when_field_missing(self):
        respx.get(f"{_API}/getChat").mock(
            return_value=httpx.Response(
                200,
                json={"ok": True, "result": {"id": 123}},
            ),
        )

        result = await check_topics_enabled(_TG)

        assert result is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_false_on_http_error(self):
        respx.get(f"{_API}/getChat").mock(
            return_value=httpx.Response(500, json={"ok": False}),
        )

        result = await check_topics_enabled(_TG)

        assert result is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_false_on_network_error(self):
        respx.get(f"{_API}/getChat").mock(
            side_effect=httpx.ConnectError("connection refused"),
        )

        result = await check_topics_enabled(_TG)

        assert result is False
