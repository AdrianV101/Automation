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


import hashlib


def _seed_master_doc_with_notes(
    vault_root: Path, target_date: date, notes_body: str,
) -> Path:
    daily = vault_root / "01-Projects" / "News" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    p = daily / f"{target_date.isoformat()}-master.md"
    p.write_text(
        f"# News Daily Master — {target_date.isoformat()}\n\n"
        "## AI\n- existing item\n\n"
        f"## Notes\n{notes_body}\n",
    )
    return p


@pytest.mark.asyncio
async def test_run_for_date_preserves_notes_section(
    db_path: Path, vault_root: Path, runner_cfg: RunnerConfig,
) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    _seed_source_item(vault_root, date(2026, 4, 29), "item-a")
    notes_body = "Adrian's annotation about today's items.\n"
    master_path = _seed_master_doc_with_notes(
        vault_root, date(2026, 4, 29), notes_body,
    )

    async def good_agent(inp: AgentRunInput) -> AgentRunOutput:
        # Simulate agent rewriting body but leaving Notes alone.
        master_path.write_text(
            f"# News Daily Master — {inp.target_date.isoformat()}\n\n"
            "## AI\n- new item\n\n"
            f"## Notes\n{notes_body}\n",
        )
        return AgentRunOutput(success=True, item_count=1, categories=["AI"])

    notifier = AsyncMock()
    await run_for_date(
        date(2026, 4, 29),
        db=db, config=runner_cfg,
        run_agent=good_agent, notify=notifier,
    )

    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "completed"


@pytest.mark.asyncio
async def test_run_for_date_detects_notes_clobber(
    db_path: Path, vault_root: Path, runner_cfg: RunnerConfig,
) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    _seed_source_item(vault_root, date(2026, 4, 29), "item-a")
    master_path = _seed_master_doc_with_notes(
        vault_root, date(2026, 4, 29), "Original annotation.\n",
    )

    async def clobbering_agent(inp: AgentRunInput) -> AgentRunOutput:
        # Agent (incorrectly) overwrites the Notes section.
        master_path.write_text(
            f"# News Daily Master — {inp.target_date.isoformat()}\n\n"
            "## AI\n- new item\n\n"
            "## Notes\nClobbered by agent.\n",
        )
        return AgentRunOutput(success=True, item_count=1, categories=["AI"])

    notifier = AsyncMock()
    await run_for_date(
        date(2026, 4, 29),
        db=db, config=runner_cfg,
        run_agent=clobbering_agent, notify=notifier,
    )

    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "failed_notes_clobbered"
    notifier.assert_awaited()
    msg = notifier.await_args.args[0]
    assert "notes" in msg.lower()


@pytest.mark.asyncio
async def test_run_for_date_verification_failure_when_agent_skipped_items(
    db_path: Path, vault_root: Path, runner_cfg: RunnerConfig,
) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    _seed_source_item(vault_root, date(2026, 4, 29), "item-a")
    _seed_source_item(vault_root, date(2026, 4, 29), "item-b")

    async def partial_agent(inp: AgentRunInput) -> AgentRunOutput:
        # Agent reports success but skipped one item — represents a
        # verification-failure mode (item-b was unparseable, etc.).
        master_path = (
            inp.vault_root / "01-Projects" / "News" / "daily"
            / f"{inp.target_date.isoformat()}-master.md"
        )
        master_path.parent.mkdir(parents=True, exist_ok=True)
        master_path.write_text(
            f"# News Daily Master — {inp.target_date.isoformat()}\n\n"
            "## AI\n- one item\n\n## Notes\n",
        )
        return AgentRunOutput(
            success=True, item_count=1, categories=["AI"],
            skipped_items=["item-b: unparseable body"],
        )

    notifier = AsyncMock()
    await run_for_date(
        date(2026, 4, 29),
        db=db, config=runner_cfg,
        run_agent=partial_agent, notify=notifier,
    )

    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "failed_verification"
    assert "item-b" in (row["error"] or "")
    notifier.assert_awaited()
