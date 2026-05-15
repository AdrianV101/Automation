from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from .clippings_state import ClippingsStateDB

log = logging.getLogger(__name__)

_PREFIX = "clip"
_OTHER = "__other__"


def encode_clip_cb(*, msg_id: int, choice_index: int) -> str:
    return f"{_PREFIX}:{msg_id}:{choice_index}"


def encode_clip_other(*, msg_id: int) -> str:
    return f"{_PREFIX}:{msg_id}:{_OTHER}"


def decode_clip_cb(data: str) -> tuple[int, int] | None:
    """(msg_id, choice_index) or None. `Other…` decodes choice_index=-1."""
    if not data or not data.startswith(f"{_PREFIX}:"):
        return None
    parts = data.split(":")
    if len(parts) != 3:
        return None
    try:
        msg_id = int(parts[1])
    except ValueError:
        return None
    if parts[2] == _OTHER:
        return (msg_id, -1)
    try:
        return (msg_id, int(parts[2]))
    except ValueError:
        return None


def build_clarification_keyboard(*, msg_id: int, candidates: list[str]) -> dict:
    rows: list[list[dict]] = []
    for i in range(0, len(candidates), 2):
        row = [
            {"text": c, "callback_data": encode_clip_cb(msg_id=msg_id, choice_index=i + j)}
            for j, c in enumerate(candidates[i:i + 2])
        ]
        rows.append(row)
    rows.append([{"text": "Other…", "callback_data": encode_clip_other(msg_id=msg_id)}])
    return {"inline_keyboard": rows}


async def handle_clip_callback(
    *,
    callback_query_id: str,
    message_id: int,
    data: str,
    state: ClippingsStateDB,
    finalize_route: Callable[..., Awaitable[None]],
    answer_callback_query: Callable[..., Awaitable[None]],
    request_free_text: Callable[..., Awaitable[None]] | None = None,
) -> None:
    """Resolve one clarification tap. Idempotent on repeated taps."""
    parsed = decode_clip_cb(data)
    if parsed is None:
        return  # not ours — dispatcher falls through to other handlers
    _, choice_index = parsed
    row = await state.find_pending_clarification_by_message(message_id)
    if row is None:
        await answer_callback_query(callback_query_id=callback_query_id, text="Expired")
        return
    candidates: list[str] = row["candidates"]

    if choice_index == -1:  # Other…
        if request_free_text is not None:
            await request_free_text(message_id=message_id)
        await answer_callback_query(callback_query_id=callback_query_id,
                                    text="Reply with the project/intent")
        return
    if not (0 <= choice_index < len(candidates)):
        await answer_callback_query(callback_query_id=callback_query_id, text="Invalid")
        return

    choice = candidates[choice_index]
    if choice.strip().lower() == "skip":
        await state.mark_skipped(row["url_key"])
        await answer_callback_query(callback_query_id=callback_query_id, text="Skipped")
        return
    await finalize_route(url_key=row["url_key"], pinned_destination=choice)
    await answer_callback_query(callback_query_id=callback_query_id, text=f"Filing → {choice}")
