from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram_interface import BotConfig
from automation_daemon.notifications import (
    answer_callback_query,
    edit_message_reply_markup,
)


def _make_tg() -> BotConfig:
    return BotConfig(bot_token="bot123", chat_id="456")


class TestAnswerCallbackQuery:
    @pytest.mark.asyncio
    async def test_calls_telegram_api(self):
        tg = _make_tg()

        with patch("automation_daemon.notifications.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp

            await answer_callback_query("cbq123", "Done!", tg)

        call_args = mock_client.post.call_args
        assert "answerCallbackQuery" in call_args[0][0]
        body = call_args[1]["json"]
        assert body["callback_query_id"] == "cbq123"
        assert body["text"] == "Done!"

    @pytest.mark.asyncio
    async def test_no_text(self):
        tg = _make_tg()

        with patch("automation_daemon.notifications.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp

            await answer_callback_query("cbq123", None, tg)

        body = mock_client.post.call_args[1]["json"]
        assert "text" not in body


class TestEditMessageReplyMarkup:
    @pytest.mark.asyncio
    async def test_removes_keyboard(self):
        tg = _make_tg()

        with patch("automation_daemon.notifications.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp

            await edit_message_reply_markup("456", 789, None, tg)

        call_args = mock_client.post.call_args
        assert "editMessageReplyMarkup" in call_args[0][0]
        body = call_args[1]["json"]
        assert body["chat_id"] == "456"
        assert body["message_id"] == 789

    @pytest.mark.asyncio
    async def test_sets_new_markup(self):
        tg = _make_tg()
        new_markup = {"inline_keyboard": [[{"text": "Ok", "callback_data": "ok"}]]}

        with patch("automation_daemon.notifications.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp

            await edit_message_reply_markup("456", 789, new_markup, tg)

        body = mock_client.post.call_args[1]["json"]
        # Telegram requires reply_markup as a JSON-serialised string in
        # the body, even when the body itself is JSON-encoded. Confirm
        # the keyboard round-trips correctly after serialisation.
        import json as _json
        assert _json.loads(body["reply_markup"]) == new_markup
