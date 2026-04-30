"""Tests for news_daily_master.runner.run_for_date."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from audio_ingest.news_daily_master.runner import (
    AgentRunInput,
    AgentRunOutput,
    RunnerConfig,
    run_for_date,
)
from audio_ingest.news_daily_master.state import NewsDailyMasterStateDB


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state.db"


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    return tmp_path / "vault"


@pytest.fixture
def runner_cfg(tmp_path: Path, vault_root: Path) -> RunnerConfig:
    return RunnerConfig(
        vault_root=vault_root,
        model="claude-opus-4-7",
        telegram_topic_id=42,
        retry_backoff_seconds=(0, 0, 0),  # no real waits in tests
    )


@pytest.mark.asyncio
async def test_run_for_date_no_source_folder_marks_skipped_empty(
    db_path: Path, vault_root: Path, runner_cfg: RunnerConfig,
) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    # vault_root/00-Inbox/news/2026-04-29 does not exist.
    agent_runner = AsyncMock()
    notifier = AsyncMock()

    await run_for_date(
        date(2026, 4, 29),
        db=db, config=runner_cfg,
        run_agent=agent_runner,
        notify=notifier,
    )

    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "skipped_empty"
    agent_runner.assert_not_called()
    notifier.assert_not_called()


@pytest.mark.asyncio
async def test_run_for_date_empty_folder_marks_skipped_empty(
    db_path: Path, vault_root: Path, runner_cfg: RunnerConfig,
) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    folder = vault_root / "00-Inbox" / "news" / "2026-04-29"
    folder.mkdir(parents=True)
    # No .md files inside.
    agent_runner = AsyncMock()
    notifier = AsyncMock()

    await run_for_date(
        date(2026, 4, 29),
        db=db, config=runner_cfg,
        run_agent=agent_runner,
        notify=notifier,
    )

    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "skipped_empty"
    agent_runner.assert_not_called()


def _seed_source_item(vault_root: Path, target_date: date, slug: str) -> Path:
    folder = vault_root / "00-Inbox" / "news" / target_date.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    note = folder / f"{slug}.md"
    note.write_text(
        "---\ntype: news-item\nsubject: test\n---\n# Test\n\nbody\n",
    )
    return note


@pytest.mark.asyncio
async def test_run_for_date_happy_path(
    db_path: Path, vault_root: Path, runner_cfg: RunnerConfig,
) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    _seed_source_item(vault_root, date(2026, 4, 29), "item-a")
    _seed_source_item(vault_root, date(2026, 4, 29), "item-b")

    agent_output = AgentRunOutput(
        success=True, item_count=2,
        categories=["AI", "Tech"], new_categories=[],
        skipped_items=[],
        text="ok",
    )
    # Simulate the agent writing the master doc as part of its run.
    async def fake_agent(inp: AgentRunInput) -> AgentRunOutput:
        master_path = (
            inp.vault_root / "01-Projects" / "News" / "daily"
            / f"{inp.target_date.isoformat()}-master.md"
        )
        master_path.parent.mkdir(parents=True, exist_ok=True)
        master_path.write_text(
            f"# News Daily Master — {inp.target_date.isoformat()}\n\n"
            "## AI\n- ...\n\n## Tech\n- ...\n\n"
            "## Notes\n",
        )
        return agent_output

    notifier = AsyncMock()
    await run_for_date(
        date(2026, 4, 29),
        db=db, config=runner_cfg,
        run_agent=fake_agent,
        notify=notifier,
    )

    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "completed"
    assert row["item_count"] == 2
    assert row["master_path"].endswith("2026-04-29-master.md")
    assert notifier.await_count == 1
    msg = notifier.await_args.args[0]
    assert "2026-04-29" in msg
    assert "2 items" in msg


@pytest.mark.asyncio
async def test_run_for_date_agent_returns_failure(
    db_path: Path, vault_root: Path, runner_cfg: RunnerConfig,
) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    _seed_source_item(vault_root, date(2026, 4, 29), "item-a")

    async def failing_agent(inp: AgentRunInput) -> AgentRunOutput:
        return AgentRunOutput(
            success=False, item_count=0, error="model said no",
        )

    notifier = AsyncMock()
    await run_for_date(
        date(2026, 4, 29),
        db=db, config=runner_cfg,
        run_agent=failing_agent,
        notify=notifier,
    )

    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "failed"
    assert row["error"] == "model said no"
    notifier.assert_awaited()
    err_msg = notifier.await_args.args[0]
    assert "failed" in err_msg.lower()
