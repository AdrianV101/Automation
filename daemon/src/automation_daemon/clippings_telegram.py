from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from .clippings_adapter import _TERMINAL  # reuse terminal-status set
from .clippings_router import route_clipping
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


def make_finalize_route(
    *,
    clippings_dir: Path,
    vault_root: Path,
    state: ClippingsStateDB,
    telegram_notifier: Callable[..., Awaitable[None]],
    list_routing_targets: Callable[[], list[str]],
    news_topic_id: int | None = None,
    model: str = "claude-opus-4-7",
) -> Callable[..., Awaitable[None]]:
    """Build the finalize callback used by the clarification handler.

    Re-runs the router with a pinned destination (or extra guidance) and
    applies the same disposition rules as process_clipping's routed branch.
    """

    # Two rapid taps can both pass the _TERMINAL re-check; the SKILL.md is replay-safe so the vault converges (worst case: a duplicate Telegram confirm + one extra agent run).
    async def finalize(
        *, url_key: str, pinned_destination: str | None = None,
        user_guidance: str | None = None,
    ) -> None:
        row = await state.get(url_key)
        if row is None or row["status"] in _TERMINAL:
            return
        path = clippings_dir / (row["original_filename"] or "")
        if not path.exists():
            log.warning("Finalize: clipping file gone for %s", url_key)
            await state.mark_failed(url_key)
            return
        outcome = await route_clipping(
            path, vault_root,
            routing_targets=list_routing_targets(),
            pinned_destination=pinned_destination,
            user_guidance=user_guidance,
            model=model,
        )
        if outcome.kind == "routed":
            await state.mark_routed(url_key, outcome.routed_path or "")
            await telegram_notifier(
                f"📎 Clipping filed: {path.name}\n→ {outcome.routed_path}\n"
                f"Links added: {outcome.links_added}",
                thread_id=news_topic_id,
            )
        else:
            await state.mark_failed(url_key)
            await telegram_notifier(
                f"⚠️ Clipping still unresolved after your input: {path.name}\n"
                f"{outcome.error or outcome.question}",
                thread_id=news_topic_id,
            )

    return finalize


async def handle_clip_text_reply(
    *,
    text: str,
    reply_to_message_id: int | None,
    state: ClippingsStateDB,
    rerun_with_guidance: Callable[..., Awaitable[None]],
) -> bool:
    """Correlate a free-text reply to a pending clarification.

    Returns True if it consumed the message (so the dispatcher stops),
    False if there's no pending clarification it could belong to.
    """
    row = None
    if reply_to_message_id is not None:
        row = await state.find_pending_clarification_by_message(reply_to_message_id)
    if row is None:
        row = await state.oldest_pending_clarification()
    if row is None:
        return False
    await rerun_with_guidance(url_key=row["url_key"], user_guidance=text)
    return True
