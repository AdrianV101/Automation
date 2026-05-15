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
