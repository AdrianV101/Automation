import asyncio
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


class TestConcurrentDispatch:
    @pytest.mark.asyncio
    async def test_slow_handler_does_not_block_subsequent_messages(self):
        """A slow on_message handler must not stop the poller from processing
        the next update — both handlers should start before either finishes."""
        tg = _make_tg()

        handler_started: list[str] = []
        handler_done: list[str] = []
        blocker = asyncio.Event()

        async def slow_handler(text: str) -> None:
            handler_started.append(text)
            await blocker.wait()
            handler_done.append(text)

        updates_response = {
            "ok": True,
            "result": [
                {
                    "update_id": 100,
                    "message": {"message_id": 10, "chat": {"id": 456}, "text": "first"},
                },
                {
                    "update_id": 101,
                    "message": {"message_id": 11, "chat": {"id": 456}, "text": "second"},
                },
            ],
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
                await asyncio.sleep(0.1)
                raise KeyboardInterrupt()
            return resp

        with patch("telegram_interface.bot.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = mock_get

            with pytest.raises(KeyboardInterrupt):
                await poll_telegram_updates(
                    tg, on_message=slow_handler, max_concurrent_dispatch=4,
                )

        # Both handlers started concurrently (proves non-blocking dispatch),
        # but neither completed (blocker never set).
        assert handler_started == ["first", "second"]
        assert handler_done == []

    @pytest.mark.asyncio
    async def test_dispatch_concurrency_capped_at_2(self):
        """With max_concurrent_dispatch=2 and 3 queued updates, the third
        handler must wait until one of the first two releases."""
        tg = _make_tg()

        in_flight = 0
        peak_in_flight = 0
        handler_started_count = 0
        release = asyncio.Event()

        async def handler(text: str) -> None:
            nonlocal in_flight, peak_in_flight, handler_started_count
            in_flight += 1
            handler_started_count += 1
            peak_in_flight = max(peak_in_flight, in_flight)
            await release.wait()
            in_flight -= 1

        updates_response = {
            "ok": True,
            "result": [
                {"update_id": 100, "message": {"message_id": 1, "chat": {"id": 456}, "text": "a"}},
                {"update_id": 101, "message": {"message_id": 2, "chat": {"id": 456}, "text": "b"}},
                {"update_id": 102, "message": {"message_id": 3, "chat": {"id": 456}, "text": "c"}},
            ],
        }

        call_count = 0

        async def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if call_count == 1:
                resp.json.return_value = updates_response
            elif call_count == 2:
                await asyncio.sleep(0.1)
                assert peak_in_flight == 2, f"expected cap=2, got peak={peak_in_flight}"
                release.set()
                await asyncio.sleep(0.1)
                raise KeyboardInterrupt()
            return resp

        with patch("telegram_interface.bot.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = mock_get

            with pytest.raises(KeyboardInterrupt):
                await poll_telegram_updates(
                    tg, on_message=handler, max_concurrent_dispatch=2,
                )

        assert handler_started_count == 3, "all 3 handlers must eventually start"
        assert peak_in_flight == 2, f"concurrency must be capped at 2, was {peak_in_flight}"


@pytest.mark.asyncio
async def test_topic_handler_error_sends_correct_message():
    """A failing topic_message handler sends "Failed to process message",
    not "Failed to process command"."""
    tg = BotConfig(bot_token="tok", chat_id="123")
    sent_texts: list[str] = []

    async def failing_topic_handler(thread_id: int, text: str) -> None:
        raise RuntimeError("boom")

    update = {
        "update_id": 1,
        "message": {
            "text": "hello",
            "chat": {"id": 123},
            "message_thread_id": 42,
        },
    }

    call_count = 0

    async def mock_get(url, *, params):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if call_count == 1:
            resp.json.return_value = {"result": [update]}
        else:
            raise KeyboardInterrupt()
        return resp

    async def mock_post(url, *, json):
        sent_texts.append(json.get("text", ""))
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {}
        return resp

    with patch("telegram_interface.bot.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = mock_get
        mock_client.post = mock_post

        with pytest.raises(KeyboardInterrupt):
            await poll_telegram_updates(
                tg, on_topic_message=failing_topic_handler,
            )

        await asyncio.sleep(0.05)

    assert any("Failed to process message" in t for t in sent_texts), (
        f"expected 'Failed to process message' in sent texts, got: {sent_texts}"
    )
    assert not any("process command" in t for t in sent_texts), (
        f"should not say 'process command' for topic handler, got: {sent_texts}"
    )
