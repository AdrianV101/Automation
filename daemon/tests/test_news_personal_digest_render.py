"""Tests for news_personal_digest.render — pure functions."""
from __future__ import annotations

import pytest

from audio_ingest.news_personal_digest.render import (
    encode_callback_data,
    decode_callback_data,
    CallbackData,
    CALLBACK_PREFIX,
)


def test_callback_prefix_is_nr() -> None:
    assert CALLBACK_PREFIX == "nr"


def test_encode_callback_data_round_trip() -> None:
    data = encode_callback_data(item_id=42, rating="thumbs_up")
    assert data == "nr:42:up"
    decoded = decode_callback_data(data)
    assert decoded == CallbackData(item_id=42, rating="thumbs_up")


def test_encode_thumbs_down_and_star() -> None:
    assert encode_callback_data(item_id=1, rating="thumbs_down") == "nr:1:dn"
    assert encode_callback_data(item_id=99, rating="star") == "nr:99:st"


def test_encode_invalid_rating_raises() -> None:
    with pytest.raises(ValueError, match="invalid rating"):
        encode_callback_data(item_id=1, rating="bogus")


def test_decode_malformed_returns_none() -> None:
    assert decode_callback_data("not-our-prefix") is None
    assert decode_callback_data("nr:abc:up") is None
    assert decode_callback_data("nr:1:bogus") is None
    assert decode_callback_data("nr:1") is None
    assert decode_callback_data("") is None


def test_callback_data_within_telegram_limit() -> None:
    """callback_data has a 64-byte limit. With max realistic id (10 digits)
    we want plenty of headroom."""
    sample = encode_callback_data(item_id=9_999_999_999, rating="thumbs_down")
    assert len(sample.encode("utf-8")) < 32
