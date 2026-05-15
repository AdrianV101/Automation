from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram_interface import BotConfig
from automation_daemon.notifications import (
    _build_inline_keyboard,
    answer_callback_query,
    edit_message_reply_markup,
    send_speaker_labeling_prompt,
    send_speaker_labeled_confirmation,
)


def _make_tg() -> BotConfig:
    return BotConfig(bot_token="bot123", chat_id="456")


class TestBuildInlineKeyboard:
    def test_no_known_names(self):
        kb = _build_inline_keyboard("c1", None)
        rows = kb["inline_keyboard"]
        # Should have just Other + Ignore
        flat = [btn for row in rows for btn in row]
        texts = [b["text"] for b in flat]
        assert "Other..." in texts
        assert "Ignore" in texts
        assert len(flat) == 2

    def test_with_known_names(self):
        kb = _build_inline_keyboard("c1", ["Alice", "Carol", "Dave"])
        rows = kb["inline_keyboard"]
        flat = [btn for row in rows for btn in row]
        texts = [b["text"] for b in flat]
        assert "Alice" in texts
        assert "Carol" in texts
        assert "Dave" in texts
        assert "Other..." in texts
        assert "Ignore" in texts
        # Check callback_data format
        alice_btn = next(b for b in flat if b["text"] == "Alice")
        assert alice_btn["callback_data"] == "c1:Alice"
        ignore_btn = next(b for b in flat if b["text"] == "Ignore")
        assert ignore_btn["callback_data"] == "c1:__ignore__"

    def test_names_in_rows_of_two(self):
        kb = _build_inline_keyboard("c1", ["Alice", "Carol", "Dave"])
        rows = kb["inline_keyboard"]
        # First row: 2 names, second row: 1 name, third row: Other+Ignore
        assert len(rows[0]) == 2
        assert len(rows[-1]) == 2  # Other + Ignore always last row

    def test_empty_known_names(self):
        kb = _build_inline_keyboard("c1", [])
        rows = kb["inline_keyboard"]
        flat = [btn for row in rows for btn in row]
        assert len(flat) == 2  # Just Other + Ignore


class TestSendSpeakerLabelingPrompt:
    @pytest.mark.asyncio
    async def test_text_only_sends_message_with_inline_keyboard(self):
        """Backward compat: no voice clip, no known_names."""
        tg = _make_tg()
        response_data = {"ok": True, "result": {"message_id": 789}}

        with patch("automation_daemon.notifications.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.json.return_value = response_data
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp

            msg_id = await send_speaker_labeling_prompt(
                "c1", "recording.ogg", "Hello there friend", tg,
            )

        assert msg_id == 789
        call_kwargs = mock_client.post.call_args[1]
        body = call_kwargs["json"]
        assert body["chat_id"] == "456"
        assert "Unknown Speaker" in body["text"]
        assert "Hello there friend" in body["text"]
        assert "inline_keyboard" in body["reply_markup"]

    @pytest.mark.asyncio
    async def test_with_known_names_builds_keyboard(self):
        tg = _make_tg()
        response_data = {"ok": True, "result": {"message_id": 789}}

        with patch("automation_daemon.notifications.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.json.return_value = response_data
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp

            msg_id = await send_speaker_labeling_prompt(
                "c1", "recording.ogg", "Hello there friend", tg,
                known_names=["Alice", "Carol"],
            )

        assert msg_id == 789
        call_kwargs = mock_client.post.call_args[1]
        body = call_kwargs["json"]
        kb = body["reply_markup"]["inline_keyboard"]
        flat = [btn for row in kb for btn in row]
        texts = [b["text"] for b in flat]
        assert "Alice" in texts
        assert "Carol" in texts

    @pytest.mark.asyncio
    async def test_with_voice_clip_sends_voice(self, tmp_path):
        tg = _make_tg()
        response_data = {"ok": True, "result": {"message_id": 789}}

        clip_path = tmp_path / "clip.ogg"
        clip_path.write_bytes(b"fake ogg data")

        with patch("automation_daemon.notifications.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.json.return_value = response_data
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp

            msg_id = await send_speaker_labeling_prompt(
                "c1", "recording.ogg", "Hello there friend", tg,
                voice_clip_path=clip_path,
            )

        assert msg_id == 789
        # sendVoice uses positional URL arg and multipart form
        call_args = mock_client.post.call_args
        url = call_args[0][0]
        assert "sendVoice" in url


class TestSendSpeakerLabeledConfirmation:
    @pytest.mark.asyncio
    async def test_sends_confirmation_and_returns_message_id(self):
        tg = _make_tg()
        response_data = {"ok": True, "result": {"message_id": 555}}

        with patch("automation_daemon.notifications.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = response_data
            mock_client.post.return_value = mock_resp

            result = await send_speaker_labeled_confirmation("Alice", tg)

        assert result == 555
        call_kwargs = mock_client.post.call_args[1]
        assert "Alice" in call_kwargs["json"]["text"]


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
