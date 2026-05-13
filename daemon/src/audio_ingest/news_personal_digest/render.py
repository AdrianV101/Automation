from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Literal

from .state import VALID_RATINGS

CALLBACK_PREFIX = "nr"

_RATING_TO_SHORT = {
    "thumbs_up": "up",
    "thumbs_down": "dn",
    "star": "st",
}
_SHORT_TO_RATING = {v: k for k, v in _RATING_TO_SHORT.items()}


@dataclass(frozen=True)
class CallbackData:
    item_id: int
    rating: Literal["thumbs_up", "thumbs_down", "star"]


def encode_callback_data(*, item_id: int, rating: str) -> str:
    if rating not in VALID_RATINGS:
        raise ValueError(f"invalid rating {rating!r}")
    return f"{CALLBACK_PREFIX}:{item_id}:{_RATING_TO_SHORT[rating]}"


def decode_callback_data(data: str) -> CallbackData | None:
    """Parse callback_data; return None on any malformation.

    Returning None (rather than raising) keeps the callback handler simple:
    one branch for 'unrecognised tap' regardless of cause (corrupted data,
    foreign prefix, invalid rating, non-numeric id).
    """
    if not data or not data.startswith(f"{CALLBACK_PREFIX}:"):
        return None
    parts = data.split(":")
    if len(parts) != 3:
        return None
    _, raw_id, raw_rating = parts
    try:
        item_id = int(raw_id)
    except ValueError:
        return None
    rating = _SHORT_TO_RATING.get(raw_rating)
    if rating is None:
        return None
    return CallbackData(item_id=item_id, rating=rating)


# ---------------------------------------------------------------------------
# Telegram message + inline keyboard rendering
# ---------------------------------------------------------------------------

TELEGRAM_MESSAGE_CHAR_LIMIT = 4096

_RATING_GLYPH = {"thumbs_up": "👍", "thumbs_down": "👎", "star": "⭐"}
_SELECTED_GLYPH = "✅"


@dataclass(frozen=True)
class DigestItem:
    id: int
    title: str
    url: str | None
    briefing: str
    why_you_care: str
    source_path: str
    position: int


@dataclass(frozen=True)
class DigestCategory:
    name: str
    emoji: str
    items: list[DigestItem]


def _esc(text: str) -> str:
    """HTML-escape a string for Telegram HTML parse mode.

    Telegram's HTML mode requires `<`, `>`, and `&` to be escaped in
    text content; quotes are only required inside attribute values.
    """
    return html.escape(text, quote=False)


def _format_item(item: DigestItem) -> str:
    # Output is Telegram HTML (parse_mode="HTML"): <b>/<i> render bold +
    # italic; bare URLs auto-link. All agent-supplied strings are
    # HTML-escaped so a stray `<`, `>`, or `&` cannot break the message.
    parts = [f"▸ <b>{_esc(item.title)}</b>", f"   {_esc(item.briefing)}"]
    parts.append(f"   <i>Why you care:</i> {_esc(item.why_you_care)}")
    if item.url:
        parts.append(f"   🔗 {_esc(item.url)}")
    return "\n".join(parts)


def _format_category_header(cat: DigestCategory) -> str:
    return f"{cat.emoji} {_esc(cat.name)}"


def _button_row(item: DigestItem, chosen: str | None = None) -> list[dict[str, str]]:
    def label(rating: str) -> str:
        if chosen == rating:
            return f"{_SELECTED_GLYPH} {item.position}"
        return f"{_RATING_GLYPH[rating]} {item.position}"
    return [
        {"text": label("thumbs_up"),
         "callback_data": encode_callback_data(item_id=item.id, rating="thumbs_up")},
        {"text": label("thumbs_down"),
         "callback_data": encode_callback_data(item_id=item.id, rating="thumbs_down")},
        {"text": label("star"),
         "callback_data": encode_callback_data(item_id=item.id, rating="star")},
    ]


def keyboard_with_selection(
    items_in_message: list[DigestItem],
    *,
    chosen: dict[int, str] | None = None,
) -> dict:
    """InlineKeyboardMarkup with optional 'chosen' rating per item.

    `chosen` maps item.id → rating literal. Items not in the dict get a
    plain unmarked button row.
    """
    chosen_map = chosen or {}
    return {
        "inline_keyboard": [
            _button_row(it, chosen=chosen_map.get(it.id))
            for it in items_in_message
        ],
    }


def _split_into_messages(
    header: str, item_blocks: list[tuple[DigestItem, str]],
    limit: int = TELEGRAM_MESSAGE_CHAR_LIMIT,
) -> list[tuple[str, list[DigestItem]]]:
    """Greedy pack item_blocks into messages under `limit`.

    First message starts with `header`. Subsequent messages of the same
    category re-use the header so the reader keeps context across splits.
    Returns list of (message_text, items_in_message).
    """
    messages: list[tuple[str, list[DigestItem]]] = []
    current_text = header
    current_items: list[DigestItem] = []
    for item, block in item_blocks:
        candidate = current_text + "\n\n" + block
        if len(candidate) > limit and current_items:
            messages.append((current_text, current_items))
            current_text = header + "\n\n" + block
            current_items = [item]
        else:
            current_text = candidate
            current_items.append(item)
    if current_items:
        messages.append((current_text, current_items))
    return messages


def build_messages(
    categories: list[DigestCategory],
) -> list[tuple[str, dict]]:
    """Render category list into Telegram messages with inline keyboards.

    One message per category by default; oversize categories get split
    while keeping each item's button row alongside the message that
    contains the item's text.
    """
    messages: list[tuple[str, dict]] = []
    for cat in categories:
        header = _format_category_header(cat)
        item_blocks = [(it, _format_item(it)) for it in cat.items]
        for text, items_in_msg in _split_into_messages(header, item_blocks):
            kb = keyboard_with_selection(items_in_msg)
            messages.append((text, kb))
    return messages
