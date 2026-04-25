from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram_interface.types import BotConfig
from telegram_interface.bot import poll_telegram_updates


def _make_tg() -> BotConfig:
    return BotConfig(bot_token="bot123", chat_id="456")


class TestPollTelegramUpdates:
    @pytest.mark.asyncio
    async def test_dispatches_reply_correctly(self):
        tg = _make_tg()
        callback = AsyncMock()

        updates_response = {
            "ok": True,
            "result": [{
                "update_id": 100,
                "message": {
                    "message_id": 10,
                    "chat": {"id": 456},
                    "text": "Carol",
                    "reply_to_message": {"message_id": 789},
                },
            }],
        }

        call_count = 0

        async def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if call_count == 1:
                resp.json.return_value = updates_response
            else:
                raise KeyboardInterrupt()
            return resp

        with patch("telegram_interface.bot.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = mock_get

            with pytest.raises(KeyboardInterrupt):
                await poll_telegram_updates(tg, callback)

        callback.assert_called_once_with(789, "Carol")

    @pytest.mark.asyncio
    async def test_ignores_non_reply_messages(self):
        tg = _make_tg()
        callback = AsyncMock()

        updates_response = {
            "ok": True,
            "result": [{
                "update_id": 100,
                "message": {
                    "message_id": 10,
                    "chat": {"id": 456},
                    "text": "Just a random message",
                },
            }],
        }

        call_count = 0

        async def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if call_count == 1:
                resp.json.return_value = updates_response
            else:
                raise KeyboardInterrupt()
            return resp

        with patch("telegram_interface.bot.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = mock_get

            with pytest.raises(KeyboardInterrupt):
                await poll_telegram_updates(tg, callback)

        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_different_chat_id(self):
        tg = _make_tg()
        callback = AsyncMock()

        updates_response = {
            "ok": True,
            "result": [{
                "update_id": 100,
                "message": {
                    "message_id": 10,
                    "chat": {"id": 999},
                    "text": "Carol",
                    "reply_to_message": {"message_id": 789},
                },
            }],
        }

        call_count = 0

        async def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if call_count == 1:
                resp.json.return_value = updates_response
            else:
                raise KeyboardInterrupt()
            return resp

        with patch("telegram_interface.bot.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = mock_get

            with pytest.raises(KeyboardInterrupt):
                await poll_telegram_updates(tg, callback)

        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_callback_error_gracefully(self):
        tg = _make_tg()
        callback = AsyncMock(side_effect=RuntimeError("db error"))

        updates_response = {
            "ok": True,
            "result": [{
                "update_id": 100,
                "message": {
                    "message_id": 10,
                    "chat": {"id": 456},
                    "text": "Carol",
                    "reply_to_message": {"message_id": 789},
                },
            }],
        }

        call_count = 0

        async def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if call_count == 1:
                resp.json.return_value = updates_response
            else:
                raise KeyboardInterrupt()
            return resp

        with patch("telegram_interface.bot.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = mock_get
            # Mock post for the error feedback message
            mock_post_resp = MagicMock()
            mock_post_resp.raise_for_status = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_post_resp)

            with pytest.raises(KeyboardInterrupt):
                await poll_telegram_updates(tg, callback)

        callback.assert_called_once()


class TestPollCallbackQueries:
    @pytest.mark.asyncio
    async def test_dispatches_callback_query(self):
        tg = _make_tg()
        reply_cb = AsyncMock()
        cbq_cb = AsyncMock()

        updates_response = {
            "ok": True,
            "result": [{
                "update_id": 200,
                "callback_query": {
                    "id": "cbq999",
                    "from": {"id": 456},
                    "message": {
                        "message_id": 50,
                        "chat": {"id": 456},
                    },
                    "data": "c1:Alice",
                },
            }],
        }

        call_count = 0

        async def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if call_count == 1:
                resp.json.return_value = updates_response
            else:
                raise KeyboardInterrupt()
            return resp

        with patch("telegram_interface.bot.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = mock_get

            with pytest.raises(KeyboardInterrupt):
                await poll_telegram_updates(tg, reply_cb, on_callback_query=cbq_cb)

        cbq_cb.assert_called_once_with("cbq999", 50, "c1:Alice")
        reply_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_includes_callback_query_in_allowed_updates(self):
        tg = _make_tg()
        reply_cb = AsyncMock()
        cbq_cb = AsyncMock()

        captured_params = {}

        async def mock_get(url, params=None):
            captured_params.update(params or {})
            raise KeyboardInterrupt()

        with patch("telegram_interface.bot.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = mock_get

            with pytest.raises(KeyboardInterrupt):
                await poll_telegram_updates(tg, reply_cb, on_callback_query=cbq_cb)

        assert "callback_query" in captured_params.get("allowed_updates", [])

    @pytest.mark.asyncio
    async def test_backward_compat_without_callback_handler(self):
        """Existing callers without on_callback_query still work."""
        tg = _make_tg()
        reply_cb = AsyncMock()

        updates_response = {
            "ok": True,
            "result": [{
                "update_id": 100,
                "message": {
                    "message_id": 10,
                    "chat": {"id": 456},
                    "text": "Carol",
                    "reply_to_message": {"message_id": 789},
                },
            }],
        }

        call_count = 0

        async def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if call_count == 1:
                resp.json.return_value = updates_response
            else:
                raise KeyboardInterrupt()
            return resp

        with patch("telegram_interface.bot.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = mock_get

            with pytest.raises(KeyboardInterrupt):
                await poll_telegram_updates(tg, reply_cb)

        reply_cb.assert_called_once_with(789, "Carol")

    @pytest.mark.asyncio
    async def test_ignores_callback_query_from_wrong_chat(self):
        tg = _make_tg()
        reply_cb = AsyncMock()
        cbq_cb = AsyncMock()

        updates_response = {
            "ok": True,
            "result": [{
                "update_id": 200,
                "callback_query": {
                    "id": "cbq999",
                    "from": {"id": 999},
                    "message": {
                        "message_id": 50,
                        "chat": {"id": 999},
                    },
                    "data": "c1:Alice",
                },
            }],
        }

        call_count = 0

        async def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if call_count == 1:
                resp.json.return_value = updates_response
            else:
                raise KeyboardInterrupt()
            return resp

        with patch("telegram_interface.bot.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = mock_get

            with pytest.raises(KeyboardInterrupt):
                await poll_telegram_updates(tg, reply_cb, on_callback_query=cbq_cb)

        cbq_cb.assert_not_called()


class TestPollerThreadRouting:
    @pytest.mark.asyncio
    async def test_message_with_thread_id_calls_topic_callback(self):
        tg = _make_tg()
        reply_cb = AsyncMock()
        topic_cb = AsyncMock()
        msg_cb = AsyncMock()

        updates_response = {
            "ok": True,
            "result": [{
                "update_id": 100,
                "message": {
                    "message_id": 10,
                    "chat": {"id": 456},
                    "text": "follow-up question",
                    "message_thread_id": 42,
                },
            }],
        }

        call_count = 0

        async def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if call_count == 1:
                resp.json.return_value = updates_response
            else:
                raise KeyboardInterrupt()
            return resp

        with patch("telegram_interface.bot.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = mock_get

            with pytest.raises(KeyboardInterrupt):
                await poll_telegram_updates(
                    tg, reply_cb,
                    on_message=msg_cb,
                    on_topic_message=topic_cb,
                )

        topic_cb.assert_called_once_with(42, "follow-up question")
        msg_cb.assert_not_called()
        reply_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_with_thread_id_but_no_callback_falls_through(self):
        tg = _make_tg()
        reply_cb = AsyncMock()
        msg_cb = AsyncMock()

        updates_response = {
            "ok": True,
            "result": [{
                "update_id": 100,
                "message": {
                    "message_id": 10,
                    "chat": {"id": 456},
                    "text": "follow-up question",
                    "message_thread_id": 42,
                },
            }],
        }

        call_count = 0

        async def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if call_count == 1:
                resp.json.return_value = updates_response
            else:
                raise KeyboardInterrupt()
            return resp

        with patch("telegram_interface.bot.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = mock_get

            with pytest.raises(KeyboardInterrupt):
                await poll_telegram_updates(
                    tg, reply_cb,
                    on_message=msg_cb,
                    # on_topic_message not provided (defaults to None)
                )

        msg_cb.assert_called_once_with("follow-up question")
        reply_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_reply_in_topic_routes_to_topic_handler(self):
        """In supergroups with topics, all topic messages have implicit reply_to.
        Topic handler takes priority over labeling."""
        tg = _make_tg()
        reply_cb = AsyncMock()
        topic_cb = AsyncMock()
        msg_cb = AsyncMock()

        updates_response = {
            "ok": True,
            "result": [{
                "update_id": 100,
                "message": {
                    "message_id": 10,
                    "chat": {"id": 456},
                    "text": "Carol",
                    "reply_to_message": {"message_id": 789},
                    "message_thread_id": 42,
                },
            }],
        }

        call_count = 0

        async def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if call_count == 1:
                resp.json.return_value = updates_response
            else:
                raise KeyboardInterrupt()
            return resp

        with patch("telegram_interface.bot.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = mock_get

            with pytest.raises(KeyboardInterrupt):
                await poll_telegram_updates(
                    tg, reply_cb,
                    on_message=msg_cb,
                    on_topic_message=topic_cb,
                )

        # Topic handler takes priority when message_thread_id is present
        topic_cb.assert_called_once_with(42, "Carol")
        reply_cb.assert_not_called()
        msg_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_reply_without_thread_id_uses_labeling(self):
        """Replies in General chat (no thread_id) still go to labeling."""
        tg = _make_tg()
        reply_cb = AsyncMock()
        topic_cb = AsyncMock()
        msg_cb = AsyncMock()

        updates_response = {
            "ok": True,
            "result": [{
                "update_id": 100,
                "message": {
                    "message_id": 10,
                    "chat": {"id": 456},
                    "text": "Carol",
                    "reply_to_message": {"message_id": 789},
                },
            }],
        }

        call_count = 0

        async def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if call_count == 1:
                resp.json.return_value = updates_response
            else:
                raise KeyboardInterrupt()
            return resp

        with patch("telegram_interface.bot.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = mock_get

            with pytest.raises(KeyboardInterrupt):
                await poll_telegram_updates(
                    tg, reply_cb,
                    on_message=msg_cb,
                    on_topic_message=topic_cb,
                )

        reply_cb.assert_called_once_with(789, "Carol")
        topic_cb.assert_not_called()
        msg_cb.assert_not_called()
