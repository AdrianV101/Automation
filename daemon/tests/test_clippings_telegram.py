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


from pathlib import Path
from automation_daemon.clippings_telegram import (
    make_finalize_route, handle_clip_text_reply,
)
from automation_daemon.clippings_router import RouteOutcome


async def test_finalize_route_routes_with_pinned_dest_and_marks_routed(tmp_path, state):
    cdir = tmp_path / "Clippings"; cdir.mkdir()
    f = cdir / "A.md"
    f.write_text('---\ntitle: t\nsource: "https://x.com/a"\n---\nb\n', encoding="utf-8")
    from automation_daemon.clippings_state import parse_clipping, clipping_key
    key = clipping_key(*parse_clipping(f))
    await state.insert_pending(key, "A.md")
    await state.set_pending_clarification(key, candidates=["Automation", "Skip"],
                                          telegram_message_id=12)
    outcome = RouteOutcome(kind="routed", routed_path="01-Projects/Automation/c/A.md",
                           links_added=1)
    notify = AsyncMock()
    with patch("automation_daemon.clippings_telegram.route_clipping",
               AsyncMock(return_value=outcome)) as rc:
        finalize = make_finalize_route(
            clippings_dir=cdir, vault_root=tmp_path, state=state,
            telegram_notifier=notify, list_routing_targets=lambda: [],
        )
        await finalize(url_key=key, pinned_destination="Automation")
    assert rc.call_args.kwargs["pinned_destination"] == "Automation"
    assert (await state.get(key))["status"] == "routed"
    notify.assert_awaited()


async def test_text_reply_correlates_to_oldest_pending_and_reroutes(tmp_path, state):
    cdir = tmp_path / "Clippings"; cdir.mkdir()
    f = cdir / "A.md"
    f.write_text('---\ntitle: t\nsource: "https://x.com/a"\n---\nb\n', encoding="utf-8")
    from automation_daemon.clippings_state import parse_clipping, clipping_key
    key = clipping_key(*parse_clipping(f))
    await state.insert_pending(key, "A.md")
    await state.set_pending_clarification(key, candidates=["Automation", "Skip"],
                                          telegram_message_id=None)
    rerun = AsyncMock()
    handled = await handle_clip_text_reply(
        text="it's for the Pretext plugin demo", reply_to_message_id=None,
        state=state, rerun_with_guidance=rerun,
    )
    assert handled is True
    rerun.assert_awaited_once()
    assert rerun.call_args.kwargs["url_key"] == key
    assert "Pretext" in rerun.call_args.kwargs["user_guidance"]


async def test_text_reply_no_pending_returns_false(tmp_path, state):
    handled = await handle_clip_text_reply(
        text="hello", reply_to_message_id=None,
        state=state, rerun_with_guidance=AsyncMock(),
    )
    assert handled is False
