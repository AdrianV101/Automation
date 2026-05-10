from __future__ import annotations

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
