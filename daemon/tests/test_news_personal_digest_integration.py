"""End-to-end: master doc + interests profile + ratings table → digest delivered."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from automation_daemon.news_personal_digest.callback import (
    DigestCallbackDeps, handle_rating_callback,
)
from automation_daemon.news_personal_digest.runner import (
    AgentRunInput, AgentRunOutput, DigestRunnerConfig, run_for_date,
)
from automation_daemon.news_personal_digest.render import (
    DigestCategory, DigestItem,
)
from automation_daemon.news_personal_digest.state import NewsDigestStateDB


@pytest.mark.asyncio
async def test_full_loop_digest_then_rating_persists_and_reshapes_next_day(
    tmp_path: Path,
) -> None:
    db = NewsDigestStateDB(tmp_path / "state.db")
    await db.init_db()

    vault = tmp_path / "vault"
    daily = vault / "01-Projects" / "News" / "daily"
    daily.mkdir(parents=True)
    (daily / "2026-05-09-master.md").write_text("# Master 2026-05-09\n")
    (daily / "2026-05-10-master.md").write_text("# Master 2026-05-10\n")

    cfg = DigestRunnerConfig(
        vault_root=vault, model="claude-opus-4-7",
        feedback_window_days=7,
    )

    # Day 1: digest with two items.
    async def day1_agent(_inp: AgentRunInput) -> AgentRunOutput:
        return AgentRunOutput(
            success=True,
            categories=[
                DigestCategory("AI", "🤖", items=[
                    DigestItem(
                        id=0, title="Anthropic 4.7", url="u1",
                        briefing="b" * 100, why_you_care="w",
                        source_path="00-Inbox/news/2026-05-09/anthropic.md",
                        position=1,
                    ),
                ]),
                DigestCategory("Finance", "💸", items=[
                    DigestItem(
                        id=0, title="Generic Series B fluff", url=None,
                        briefing="b" * 100, why_you_care="w",
                        source_path="00-Inbox/news/2026-05-09/series-b.md",
                        position=1,
                    ),
                ]),
            ],
            rating_signal_summary="Day 1: no prior ratings.",
        )

    sent: list[tuple[str, dict]] = []

    async def send_messages(messages):
        sent.extend(messages)
        return [10001 + i for i in range(len(messages))]

    await run_for_date(
        date(2026, 5, 9), db=db, config=cfg,
        run_agent=day1_agent, notify=AsyncMock(),
        send_messages=send_messages,
    )

    row = await db.get_run(date(2026, 5, 9))
    assert row["status"] == "completed"
    assert row["item_count"] == 2

    item_anthropic = await db.get_digest_item(1)
    item_series_b = await db.get_digest_item(2)
    assert item_anthropic["title"] == "Anthropic 4.7"
    assert item_series_b["title"] == "Generic Series B fluff"
    assert item_anthropic["telegram_message_id"] == 10001
    assert item_series_b["telegram_message_id"] == 10002

    # Simulate a 👎 tap on Series B, ⭐ on Anthropic.
    edit_kb = AsyncMock()
    answer = AsyncMock()
    deps = DigestCallbackDeps(
        db=db, edit_message_reply_markup=edit_kb,
        answer_callback_query=answer,
    )
    await handle_rating_callback(
        callback_query_id="c1", message_id=10001,
        data="nr:1:st", deps=deps,
    )
    await handle_rating_callback(
        callback_query_id="c2", message_id=10002,
        data="nr:2:dn", deps=deps,
    )
    assert (await db.get_rating(1))["rating"] == "star"
    assert (await db.get_rating(2))["rating"] == "thumbs_down"

    # Day 2: prompt to the agent must include those ratings as feedback.
    captured: dict[str, str] = {}

    async def day2_agent(inp: AgentRunInput) -> AgentRunOutput:
        captured["prompt"] = inp.prompt
        return AgentRunOutput(
            success=True, categories=[],
            rating_signal_summary="Day 2: deprioritised generic Series B.",
        )

    await run_for_date(
        date(2026, 5, 10), db=db, config=cfg,
        run_agent=day2_agent, notify=AsyncMock(),
        send_messages=AsyncMock(return_value=[]),
    )

    row2 = await db.get_run(date(2026, 5, 10))
    assert row2["status"] == "completed"
    assert "deprioritised" in (row2["rating_signal_summary"] or "")
    # The day-2 prompt carried the day-1 ratings.
    assert "Anthropic 4.7" in captured["prompt"]
    assert "Generic Series B fluff" in captured["prompt"]
    assert "⭐" in captured["prompt"]
    assert "👎" in captured["prompt"]
