"""Tests for news_personal_digest.render — pure functions."""
from __future__ import annotations

import pytest

from automation_daemon.news_personal_digest.render import (
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


# ---------------------------------------------------------------------------
# Message + keyboard builders
# ---------------------------------------------------------------------------

from automation_daemon.news_personal_digest.render import (  # noqa: E402
    DigestCategory,
    DigestItem,
    build_messages,
    keyboard_with_selection,
)


def _item(*, id: int = 0, title: str = "T", url: str | None = "https://x",
          briefing: str = "Body.", why: str = "Because.",
          source_path: str = "p", position: int = 1) -> DigestItem:
    return DigestItem(
        id=id, title=title, url=url, briefing=briefing,
        why_you_care=why, source_path=source_path, position=position,
    )


def test_build_messages_one_category_one_item() -> None:
    cat = DigestCategory(
        name="AI", emoji="🤖",
        items=[_item(id=1, title="Anthropic 4.7", position=1)],
    )
    messages = build_messages([cat])
    assert len(messages) == 1
    text, keyboard = messages[0]
    assert "🤖 AI" in text
    assert "Anthropic 4.7" in text
    assert "🔗" in text
    assert len(keyboard["inline_keyboard"]) == 1
    row = keyboard["inline_keyboard"][0]
    assert [b["text"] for b in row] == ["👍 1", "👎 1", "⭐ 1"]
    assert [b["callback_data"] for b in row] == [
        "nr:1:up", "nr:1:dn", "nr:1:st",
    ]


def test_build_messages_one_category_three_items_three_rows() -> None:
    cat = DigestCategory(
        name="AI", emoji="🤖",
        items=[
            _item(id=10, title="One", position=1),
            _item(id=11, title="Two", position=2),
            _item(id=12, title="Three", position=3),
        ],
    )
    messages = build_messages([cat])
    assert len(messages) == 1
    text, kb = messages[0]
    assert "One" in text and "Two" in text and "Three" in text
    assert len(kb["inline_keyboard"]) == 3
    assert kb["inline_keyboard"][0][0]["text"] == "👍 1"
    assert kb["inline_keyboard"][1][0]["text"] == "👍 2"
    assert kb["inline_keyboard"][2][0]["text"] == "👍 3"
    assert all(
        b["callback_data"].startswith("nr:10:")
        for b in kb["inline_keyboard"][0]
    )


def test_build_messages_multiple_categories_one_message_each() -> None:
    cats = [
        DigestCategory("AI", "🤖", [_item(id=1, title="A", position=1)]),
        DigestCategory("Finance", "💸", [_item(id=2, title="B", position=1)]),
        DigestCategory("Tech", "🛠", [_item(id=3, title="C", position=1)]),
    ]
    messages = build_messages(cats)
    assert len(messages) == 3
    assert "🤖 AI" in messages[0][0]
    assert "💸 Finance" in messages[1][0]
    assert "🛠 Tech" in messages[2][0]


def test_build_messages_item_without_url_omits_link_line() -> None:
    cat = DigestCategory(
        "AI", "🤖", [_item(id=1, url=None, title="X", position=1)],
    )
    text, _ = build_messages([cat])[0]
    assert "🔗" not in text


def test_build_messages_splits_oversized_category() -> None:
    """Categories whose rendered text exceeds Telegram's per-message limit
    are split. Button rows track items correctly across split messages."""
    big_briefing = "x" * 1500
    cat = DigestCategory(
        "AI", "🤖",
        items=[
            _item(id=1, title="A", briefing=big_briefing, position=1),
            _item(id=2, title="B", briefing=big_briefing, position=2),
            _item(id=3, title="C", briefing=big_briefing, position=3),
        ],
    )
    messages = build_messages([cat])
    assert len(messages) >= 2
    for text, _ in messages:
        assert len(text) <= 4096
    all_rows = [row for _, kb in messages for row in kb["inline_keyboard"]]
    assert len(all_rows) == 3


def test_keyboard_with_selection_marks_chosen_button() -> None:
    items_in_msg = [_item(id=1, position=1), _item(id=2, position=2)]
    kb = keyboard_with_selection(
        items_in_msg, chosen={1: "thumbs_up"},
    )
    row1, row2 = kb["inline_keyboard"]
    assert row1[0]["text"] == "✅ 1"
    assert row1[1]["text"] == "👎 1"
    assert row1[2]["text"] == "⭐ 1"
    assert row2[0]["text"] == "👍 2"
    assert row2[1]["text"] == "👎 2"
    assert row2[2]["text"] == "⭐ 2"


def test_keyboard_with_selection_thumbs_down_marks_correct_button() -> None:
    items_in_msg = [_item(id=1, position=1)]
    kb = keyboard_with_selection(items_in_msg, chosen={1: "thumbs_down"})
    row = kb["inline_keyboard"][0]
    assert row[0]["text"] == "👍 1"
    assert row[1]["text"] == "✅ 1"
    assert row[2]["text"] == "⭐ 1"


# ---------------------------------------------------------------------------
# Telegram HTML output contract
# ---------------------------------------------------------------------------
# Messages are sent with parse_mode="HTML"; the renderer must emit valid
# Telegram HTML (not raw markdown) and HTML-escape agent-supplied content.


def test_build_messages_emits_html_bold_for_title() -> None:
    cat = DigestCategory(
        "AI", "🤖", [_item(id=1, title="Anthropic 4.7", position=1)],
    )
    text, _ = build_messages([cat])[0]
    assert "<b>Anthropic 4.7</b>" in text
    # No raw markdown bold markers should leak through.
    assert "**" not in text


def test_build_messages_emits_html_italic_for_why_you_care() -> None:
    cat = DigestCategory(
        "AI", "🤖",
        [_item(id=1, why="It matters because X.", position=1)],
    )
    text, _ = build_messages([cat])[0]
    assert "<i>Why you care:</i>" in text
    # No raw markdown italic markers around the label.
    assert "_Why you care:_" not in text


def test_build_messages_html_escapes_agent_supplied_content() -> None:
    """Agent text may contain `<`, `>`, `&`. Escape so parse_mode="HTML"
    does not interpret it as a tag and reject the message."""
    cat = DigestCategory(
        "AI & ML", "🤖",
        [_item(
            id=1,
            title="A<script>",
            briefing="x & y < z",
            why="<not-a-tag>",
            url="https://example.com/?a=1&b=2",
            position=1,
        )],
    )
    text, _ = build_messages([cat])[0]
    # Category header
    assert "AI &amp; ML" in text
    # Title (inside <b> wrapper)
    assert "<b>A&lt;script&gt;</b>" in text
    # Briefing escaped
    assert "x &amp; y &lt; z" in text
    # Why-you-care escaped
    assert "&lt;not-a-tag&gt;" in text
    # URL escaped (& → &amp;); bare URLs auto-link in HTML mode.
    assert "https://example.com/?a=1&amp;b=2" in text
    # Raw unescaped reserved characters must not remain in agent content.
    # (The literal `<` and `>` in `<b>` and `<i>` tags are fine, so check
    # by ensuring the dangerous substrings don't appear.)
    assert "<script>" not in text
    assert "<not-a-tag>" not in text


def test_build_messages_has_no_raw_markdown_artifacts() -> None:
    """Sanity check: with parse_mode="HTML" we must not be emitting any
    of the old markdown formatting characters in structural positions."""
    cat = DigestCategory(
        "AI", "🤖", [_item(id=1, title="T", why="W", position=1)],
    )
    text, _ = build_messages([cat])[0]
    assert "**" not in text
    # Underscore-as-italic syntax must not appear around the label.
    assert "_Why" not in text and "care:_" not in text
