"""Tests for news_personal_digest.callback — Telegram rating-button handler."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from audio_ingest.news_personal_digest.callback import (
    DigestCallbackDeps,
    handle_rating_callback,
)
from audio_ingest.news_personal_digest.state import (
    DigestItemInput, NewsDigestStateDB,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state.db"


async def _seed(db_path: Path) -> tuple[NewsDigestStateDB, int, int]:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    await db.insert_run(digest_date=date(2026, 5, 9))
    id1 = await db.insert_item(
        digest_date=date(2026, 5, 9),
        item=DigestItemInput("p1", "AI", "Anthropic 4.7", "u1", 1),
    )
    id2 = await db.insert_item(
        digest_date=date(2026, 5, 9),
        item=DigestItemInput("p2", "AI", "OpenAI safety", "u2", 2),
    )
    await db.attach_telegram_message_id(
        item_ids=[id1, id2], telegram_message_id=10001,
    )
    return db, id1, id2


@pytest.mark.asyncio
async def test_valid_callback_persists_rating_and_edits_keyboard(
    db_path: Path,
) -> None:
    db, id1, id2 = await _seed(db_path)

    edit_message_reply_markup = AsyncMock()
    answer_callback_query = AsyncMock()
    deps = DigestCallbackDeps(
        db=db,
        edit_message_reply_markup=edit_message_reply_markup,
        answer_callback_query=answer_callback_query,
    )

    await handle_rating_callback(
        callback_query_id="cbq-1",
        message_id=10001,
        data=f"nr:{id1}:up",
        deps=deps,
    )

    rating = await db.get_rating(id1)
    assert rating is not None
    assert rating["rating"] == "thumbs_up"
    edit_message_reply_markup.assert_awaited_once()
    args = edit_message_reply_markup.call_args
    new_keyboard = args.kwargs["reply_markup"]
    rows = new_keyboard["inline_keyboard"]
    assert len(rows) == 2
    assert rows[0][0]["text"] == "✅ 1"
    assert rows[1][0]["text"] == "👍 2"
    answer_callback_query.assert_awaited_once()


@pytest.mark.asyncio
async def test_double_tap_overwrites_rating(db_path: Path) -> None:
    db, id1, _ = await _seed(db_path)
    deps = DigestCallbackDeps(
        db=db,
        edit_message_reply_markup=AsyncMock(),
        answer_callback_query=AsyncMock(),
    )
    await handle_rating_callback(
        callback_query_id="c1", message_id=10001,
        data=f"nr:{id1}:up", deps=deps,
    )
    await handle_rating_callback(
        callback_query_id="c2", message_id=10001,
        data=f"nr:{id1}:dn", deps=deps,
    )
    rating = await db.get_rating(id1)
    assert rating["rating"] == "thumbs_down"


@pytest.mark.asyncio
async def test_unknown_item_id_answers_expired_no_db_write(
    db_path: Path,
) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    edit = AsyncMock()
    answer = AsyncMock()
    await handle_rating_callback(
        callback_query_id="cbq",
        message_id=999,
        data="nr:99999:up",
        deps=DigestCallbackDeps(
            db=db,
            edit_message_reply_markup=edit,
            answer_callback_query=answer,
        ),
    )
    answer.assert_awaited()
    text = answer.call_args.kwargs.get("text", "")
    assert "expired" in text.lower()
    edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_callback_data_answers_invalid(
    db_path: Path,
) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    edit = AsyncMock()
    answer = AsyncMock()
    await handle_rating_callback(
        callback_query_id="cbq",
        message_id=10001,
        data="nr:abc:bogus",
        deps=DigestCallbackDeps(
            db=db,
            edit_message_reply_markup=edit,
            answer_callback_query=answer,
        ),
    )
    answer.assert_awaited()
    edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_tap_on_unattached_item_skips_keyboard_edit(
    db_path: Path,
) -> None:
    """Race guard: if a tap arrives for an item whose telegram_message_id
    is NULL (send-failed or pre-attachment), we MUST NOT send Telegram an
    empty keyboard — it would wipe all buttons from the message. Rating
    must still persist."""
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    await db.insert_run(digest_date=date(2026, 5, 9))
    item_id = await db.insert_item(
        digest_date=date(2026, 5, 9),
        item=DigestItemInput("p1", "AI", "Unattached", "u1", 1),
    )
    # NOTE: no attach_telegram_message_id — item is orphan.

    edit = AsyncMock()
    answer = AsyncMock()
    await handle_rating_callback(
        callback_query_id="cbq",
        message_id=10001,
        data=f"nr:{item_id}:up",
        deps=DigestCallbackDeps(
            db=db,
            edit_message_reply_markup=edit,
            answer_callback_query=answer,
        ),
    )
    rating = await db.get_rating(item_id)
    assert rating is not None
    assert rating["rating"] == "thumbs_up"
    edit.assert_not_awaited()  # guard prevented the wipe
    answer.assert_awaited()


@pytest.mark.asyncio
async def test_edit_failure_does_not_block_persist(db_path: Path) -> None:
    """Telegram edit can fail (message deleted etc.) — rating still persists."""
    db, id1, _ = await _seed(db_path)

    edit = AsyncMock(side_effect=RuntimeError("message not found"))
    answer = AsyncMock()
    await handle_rating_callback(
        callback_query_id="cbq",
        message_id=10001,
        data=f"nr:{id1}:st",
        deps=DigestCallbackDeps(
            db=db,
            edit_message_reply_markup=edit,
            answer_callback_query=answer,
        ),
    )
    rating = await db.get_rating(id1)
    assert rating["rating"] == "star"
    answer.assert_awaited()
