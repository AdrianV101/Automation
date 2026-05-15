import pytest
from unittest.mock import AsyncMock, patch

from automation_daemon.clippings_telegram import (
    build_clarification_keyboard, encode_clip_cb, decode_clip_cb,
    handle_clip_callback,
)
from automation_daemon.clippings_state import ClippingsStateDB


def test_encode_decode_roundtrip():
    data = encode_clip_cb(msg_id=42, choice_index=1)
    parsed = decode_clip_cb(data)
    assert parsed == (42, 1)


def test_decode_rejects_foreign_prefix():
    assert decode_clip_cb("digest:1:u") is None
    assert decode_clip_cb("clip:notanint:x") is None


def test_build_keyboard_has_candidate_buttons_plus_other():
    kb = build_clarification_keyboard(msg_id=7, candidates=["Next Steps", "Automation"])
    rows = kb["inline_keyboard"]
    flat = [b for row in rows for b in row]
    labels = [b["text"] for b in flat]
    assert "Next Steps" in labels and "Automation" in labels
    assert any("Other" in l for l in labels)
    for b in flat:
        assert decode_clip_cb(b["callback_data"]) is not None


@pytest.fixture
async def state(tmp_path):
    d = ClippingsStateDB(tmp_path / "s.db")
    await d.init_db()
    return d


async def test_callback_pins_destination_and_finalizes(tmp_path, state):
    await state.insert_pending("k", "A.md")
    await state.set_pending_clarification("k", candidates=["Next Steps", "Skip"],
                                          telegram_message_id=99)
    finalize = AsyncMock()
    answer = AsyncMock()
    await handle_clip_callback(
        callback_query_id="cq", message_id=99,
        data=encode_clip_cb(msg_id=99, choice_index=0),
        state=state, finalize_route=finalize, answer_callback_query=answer,
    )
    finalize.assert_awaited_once()
    assert finalize.call_args.kwargs["pinned_destination"] == "Next Steps"
    answer.assert_awaited()


async def test_callback_skip_marks_skipped(tmp_path, state):
    await state.insert_pending("k", "A.md")
    await state.set_pending_clarification("k", candidates=["Next Steps", "Skip"],
                                          telegram_message_id=99)
    await handle_clip_callback(
        callback_query_id="cq", message_id=99,
        data=encode_clip_cb(msg_id=99, choice_index=1),  # "Skip"
        state=state, finalize_route=AsyncMock(), answer_callback_query=AsyncMock(),
    )
    assert (await state.get("k"))["status"] == "skipped"
