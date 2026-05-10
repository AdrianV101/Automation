"""Tests for news_personal_digest.runner — orchestration with mocked deps."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from audio_ingest.news_personal_digest.runner import (
    AgentRunInput,
    AgentRunOutput,
    DigestRunnerConfig,
    run_for_date,
)
from audio_ingest.news_personal_digest.state import (
    DigestItemInput,
    NewsDigestStateDB,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state.db"


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "01-Projects" / "News" / "daily").mkdir(parents=True)
    return root


@pytest.fixture
def cfg(vault_root: Path) -> DigestRunnerConfig:
    return DigestRunnerConfig(
        vault_root=vault_root,
        model="claude-opus-4-7",
        feedback_window_days=7,
    )


@pytest.mark.asyncio
async def test_skipped_no_master_when_master_doc_absent(
    db_path: Path, cfg: DigestRunnerConfig,
) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    run_agent = AsyncMock()
    notify = AsyncMock()
    send_messages = AsyncMock()

    await run_for_date(
        date(2026, 5, 9),
        db=db, config=cfg,
        run_agent=run_agent, notify=notify, send_messages=send_messages,
    )

    row = await db.get_run(date(2026, 5, 9))
    assert row["status"] == "skipped_no_master"
    run_agent.assert_not_awaited()
    send_messages.assert_not_awaited()
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexpected_exception_marks_failed_and_alerts(
    db_path: Path, cfg: DigestRunnerConfig,
) -> None:
    """Belt-and-braces: any uncaught exception in the inner path leaves the
    row as 'failed' (never stuck 'running') and emits a Telegram alert."""
    db = NewsDigestStateDB(db_path)
    await db.init_db()

    async def boom(_inp: AgentRunInput) -> AgentRunOutput:
        raise RuntimeError("kaboom")

    notify = AsyncMock()
    send_messages = AsyncMock()
    master = (cfg.vault_root / "01-Projects" / "News" / "daily"
              / "2026-05-09-master.md")
    master.write_text("# News Daily Master\n")

    await run_for_date(
        date(2026, 5, 9),
        db=db, config=cfg,
        run_agent=boom, notify=notify, send_messages=send_messages,
    )

    row = await db.get_run(date(2026, 5, 9))
    assert row["status"] == "failed"
    assert "kaboom" in (row["error"] or "")
    notify.assert_awaited()
