from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .render import (
    DigestItem, decode_callback_data, keyboard_with_selection,
)
from .state import NewsDigestStateDB

log = logging.getLogger(__name__)


# Async callable signatures injected by the daemon orchestrator from
# notifications.py — kept abstract so unit tests don't need real Telegram.
EditReplyMarkupFn = Callable[..., Awaitable[Any]]
AnswerCallbackQueryFn = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class DigestCallbackDeps:
    db: NewsDigestStateDB
    edit_message_reply_markup: EditReplyMarkupFn
    answer_callback_query: AnswerCallbackQueryFn


_RATING_TOAST_GLYPH = {
    "thumbs_up": "👍", "thumbs_down": "👎", "star": "⭐",
}


async def handle_rating_callback(
    *,
    callback_query_id: str,
    message_id: int,
    data: str,
    deps: DigestCallbackDeps,
) -> None:
    """Handle one Telegram callback_query whose data has the digest prefix.

    Idempotent — repeated taps with the same data leave one row per item
    (PK on item_id, ON CONFLICT DO UPDATE).
    """
    parsed = decode_callback_data(data)
    if parsed is None:
        log.warning("Malformed digest callback_data: %r", data)
        await deps.answer_callback_query(
            callback_query_id=callback_query_id, text="Invalid",
        )
        return

    row = await deps.db.get_digest_item(parsed.item_id)
    if row is None:
        log.warning("Digest callback for unknown item_id=%d", parsed.item_id)
        await deps.answer_callback_query(
            callback_query_id=callback_query_id, text="Item expired",
        )
        return

    # Persist first — Telegram edit failure must not lose the rating.
    try:
        await deps.db.upsert_rating(
            item_id=parsed.item_id, rating=parsed.rating,
        )
    except Exception:
        log.exception("Failed to upsert rating for item_id=%d", parsed.item_id)
        await deps.answer_callback_query(
            callback_query_id=callback_query_id, text="Couldn't save",
        )
        return

    # Build a fresh keyboard reflecting all current ratings on items in the
    # message. Other items in the message keep their (possibly older) chosen
    # state visually consistent.
    items_in_message = await _items_in_message(
        deps.db, telegram_message_id=row["telegram_message_id"],
    )
    chosen = await _chosen_map(deps.db, items=items_in_message)

    try:
        await deps.edit_message_reply_markup(
            message_id=message_id,
            reply_markup=keyboard_with_selection(items_in_message, chosen=chosen),
        )
    except Exception:
        log.warning(
            "edit_message_reply_markup failed for message_id=%d (rating "
            "persisted)", message_id, exc_info=True,
        )

    glyph = _RATING_TOAST_GLYPH[parsed.rating]
    await deps.answer_callback_query(
        callback_query_id=callback_query_id, text=f"Rated {glyph}",
    )


async def _items_in_message(
    db: NewsDigestStateDB, *, telegram_message_id: int | None,
) -> list[DigestItem]:
    """All digest items attached to the given Telegram message, sorted by position.

    Returns [] if telegram_message_id is None (item not yet attached).
    """
    if telegram_message_id is None:
        return []
    rows = await db.list_items_for_message(telegram_message_id)
    return [
        DigestItem(
            id=int(r["id"]),
            title=r["title"],
            url=r["url"],
            briefing="",  # not needed for keyboard rebuild
            why_you_care="",
            source_path=r["source_path"],
            position=int(r["position"]),
        )
        for r in rows
    ]


async def _chosen_map(
    db: NewsDigestStateDB, *, items: list[DigestItem],
) -> dict[int, str]:
    out: dict[int, str] = {}
    for it in items:
        rating = await db.get_rating(it.id)
        if rating is not None:
            out[it.id] = rating["rating"]
    return out
