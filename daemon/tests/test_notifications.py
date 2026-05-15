"""Tests for automation_daemon.notifications — speaker keyboard builder."""
from automation_daemon.notifications import build_speaker_keyboard
from automation_daemon.speaker_resolution import decode_choice


def test_keyboard_uses_sr_tokens_and_has_other_ignore() -> None:
    kb = build_speaker_keyboard(speaker_idx=2, known_names=["Alice", "Bob"])
    rows = kb["inline_keyboard"]
    name_btns = [b for row in rows for b in row if b["text"] in ("Alice", "Bob")]
    assert len(name_btns) == 2
    for b in name_btns:
        assert decode_choice(b["callback_data"])[0] == 2
        assert len(b["callback_data"].encode()) <= 64
    last = rows[-1]
    assert {b["text"] for b in last} == {"Other...", "Not sure"}
    assert decode_choice(last[0]["callback_data"]) == (2, "__other__")
    assert decode_choice(last[1]["callback_data"]) == (2, "__ignore__")


def test_keyboard_truncates_long_name_payload() -> None:
    kb = build_speaker_keyboard(speaker_idx=0, known_names=["X" * 200])
    btn = kb["inline_keyboard"][0][0]
    assert len(btn["callback_data"].encode()) <= 64


import httpx
import pytest
import respx

from telegram_interface import BotConfig, TELEGRAM_API
from automation_daemon.notifications import send_speaker_prompt


@pytest.mark.asyncio
@respx.mock
async def test_send_speaker_prompt_posts_text_with_keyboard_and_reminder() -> None:
    route = respx.post(f"{TELEGRAM_API}/botT/sendMessage").mock(
        return_value=httpx.Response(200, json={"result": {"message_id": 555}}),
    )
    bot = BotConfig(bot_token="T", chat_id="C")
    mid = await send_speaker_prompt(
        speaker_idx=0, speaker_label="Speaker 1",
        recording_name="Standup", sample_text="we shipped the gate",
        tg=bot, known_names=["Alice"], thread_id=7,
    )
    assert mid == 555
    body = route.calls[0].request.content.decode()
    assert "Speaker 1" in body and "Plaud app" in body and "we shipped the gate" in body
