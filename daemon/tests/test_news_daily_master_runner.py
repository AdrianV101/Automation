"""Tests for news_daily_master.runner.run_for_date."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from audio_ingest.news_daily_master.runner import run_for_date, RunnerConfig
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
