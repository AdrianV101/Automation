"""End-to-end integration test for news-daily-master.

Exercises real state DB + real runner orchestration with a fake agent that
simulates MCP edits by writing files directly to the temp vault. Covers the
full happy path including notes-section preservation across re-run.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from audio_ingest.news_daily_master.runner import (
    AgentRunInput, AgentRunOutput, RunnerConfig, run_for_date,
)
from audio_ingest.news_daily_master.state import NewsDailyMasterStateDB


def _seed_news_items(vault: Path, target: date, slugs: list[str]) -> None:
    folder = vault / "00-Inbox" / "news" / target.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    for slug in slugs:
        (folder / f"{slug}.md").write_text(
            "---\ntype: news-item\nsubject: test\n---\n# T\n\nbody\n",
        )


def _read_master(vault: Path, target: date) -> str:
    return (
        vault / "01-Projects" / "News" / "daily"
        / f"{target.isoformat()}-master.md"
    ).read_text()


@pytest.mark.asyncio
async def test_full_run_first_time(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    vault = tmp_path / "vault"
    target = date(2026, 4, 29)
    _seed_news_items(vault, target, ["item-a", "item-b", "item-c"])

    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()

    async def fake_agent(inp: AgentRunInput) -> AgentRunOutput:
        # Simulate the agent writing the master doc and updating frontmatter.
        master = (
            inp.vault_root / "01-Projects" / "News" / "daily"
            / f"{inp.target_date.isoformat()}-master.md"
        )
        master.parent.mkdir(parents=True, exist_ok=True)
        master.write_text(
            f"# News Daily Master — {inp.target_date.isoformat()}\n\n"
            "## AI\n"
            "- **[A](https://x)** — [[00-Inbox/news/2026-04-29/item-a|src]] — "
            "summary [[01-Projects/News/entities/Anthropic|Anthropic]]\n\n"
            "## Tech\n"
            "- **[B](https://y)** — [[00-Inbox/news/2026-04-29/item-b|src]] — sum\n\n"
            "## Notes\n",
        )
        return AgentRunOutput(
            success=True, item_count=3,
            categories=["AI", "Tech"], new_categories=[], skipped_items=[],
        )

    notifications: list[str] = []
    async def notify(text: str) -> None:
        notifications.append(text)

    cfg = RunnerConfig(
        vault_root=vault, model="claude-opus-4-7",
        telegram_topic_id=42, retry_backoff_seconds=(0,),
    )
    await run_for_date(
        target, db=db, config=cfg, run_agent=fake_agent, notify=notify,
    )

    row = await db.get_run(target)
    assert row["status"] == "completed"
    assert row["item_count"] == 3
    body = _read_master(vault, target)
    assert "# News Daily Master — 2026-04-29" in body
    assert "## Notes" in body
    assert any("master ready" in n for n in notifications)


@pytest.mark.asyncio
async def test_rerun_preserves_human_notes(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    vault = tmp_path / "vault"
    target = date(2026, 4, 29)
    _seed_news_items(vault, target, ["item-a"])

    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()

    daily = vault / "01-Projects" / "News" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    notes_body = (
        "## Notes\nAdrian's annotation from yesterday — must survive re-run.\n"
    )
    (daily / f"{target.isoformat()}-master.md").write_text(
        f"# News Daily Master — {target.isoformat()}\n\n"
        "## AI\n- old item\n\n" + notes_body,
    )

    async def fake_agent(inp: AgentRunInput) -> AgentRunOutput:
        master = (
            inp.vault_root / "01-Projects" / "News" / "daily"
            / f"{inp.target_date.isoformat()}-master.md"
        )
        # Rewrite body but preserve Notes verbatim.
        master.write_text(
            f"# News Daily Master — {inp.target_date.isoformat()}\n\n"
            "## AI\n- old item\n- new item\n\n"
            + notes_body,
        )
        return AgentRunOutput(
            success=True, item_count=2,
            categories=["AI"], new_categories=[], skipped_items=[],
        )

    cfg = RunnerConfig(
        vault_root=vault, model="claude-opus-4-7", telegram_topic_id=42,
        retry_backoff_seconds=(0,),
    )
    notifications: list[str] = []
    async def notify(t: str) -> None:
        notifications.append(t)

    await run_for_date(
        target, db=db, config=cfg, run_agent=fake_agent, notify=notify,
    )

    row = await db.get_run(target)
    assert row["status"] == "completed"
    body = _read_master(vault, target)
    assert "Adrian's annotation from yesterday — must survive re-run." in body
