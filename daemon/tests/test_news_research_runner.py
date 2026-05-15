from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from audio_ingest.news_research.runner import (
    AgentRunInput,
    AgentRunOutput,
    RunnerConfig,
    run_for_date,
)
from audio_ingest.news_research.state import NewsResearchStateDB


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state.db"


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    return tmp_path / "vault"


@pytest.fixture
def runner_cfg(vault_root: Path) -> RunnerConfig:
    return RunnerConfig(
        vault_root=vault_root,
        model="claude-sonnet-4-6",
        max_items=3,
        feedback_window_days=7,
        retry_backoff_seconds=(0, 0),  # no real waits in tests
    )


def _seed_master(vault_root: Path, d: date, notes_body: str = "") -> Path:
    folder = vault_root / "01-Projects" / "News" / "daily"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{d.isoformat()}-master.md"
    p.write_text(
        f"# News Daily Master — {d.isoformat()}\n\n"
        f"## AI\n\n- **Thing** — source — summary.\n\n"
        f"## Notes\n\n<!-- human -->\n{notes_body}"
    )
    return p


async def _no_ratings(start: date, end: date) -> list[dict]:
    return []


def test_agent_run_output_pair_invariant() -> None:
    with pytest.raises(ValueError):
        AgentRunOutput(success=True, error="x")
    with pytest.raises(ValueError):
        AgentRunOutput(success=False)  # error required


@pytest.mark.asyncio
async def test_skips_when_master_absent(
    db_path: Path, runner_cfg: RunnerConfig,
) -> None:
    db = NewsResearchStateDB(db_path)
    await db.init_db()
    agent = AsyncMock()

    await run_for_date(
        date(2026, 5, 15), db=db, config=runner_cfg,
        run_agent=agent, recent_ratings=_no_ratings,
    )

    row = await db.get_run(date(2026, 5, 15))
    assert row["status"] == "skipped_no_master"
    agent.assert_not_called()


@pytest.mark.asyncio
async def test_happy_path_marks_completed_with_metrics(
    db_path: Path, runner_cfg: RunnerConfig, vault_root: Path,
) -> None:
    db = NewsResearchStateDB(db_path)
    await db.init_db()
    _seed_master(vault_root, date(2026, 5, 15))

    async def fake_agent(inp: AgentRunInput) -> AgentRunOutput:
        return AgentRunOutput(
            success=True, items_researched=2, cost_usd=0.31, turns_used=12,
        )

    await run_for_date(
        date(2026, 5, 15), db=db, config=runner_cfg,
        run_agent=fake_agent, recent_ratings=_no_ratings,
    )

    row = await db.get_run(date(2026, 5, 15))
    assert row["status"] == "completed"
    assert row["items_researched"] == 2
    assert row["cost_usd"] == pytest.approx(0.31)
    assert row["turns_used"] == 12


@pytest.mark.asyncio
async def test_ratings_window_passed_to_provider(
    db_path: Path, runner_cfg: RunnerConfig, vault_root: Path,
) -> None:
    db = NewsResearchStateDB(db_path)
    await db.init_db()
    _seed_master(vault_root, date(2026, 5, 15))
    seen: dict[str, date] = {}

    async def spy_ratings(start: date, end: date) -> list[dict]:
        seen["start"], seen["end"] = start, end
        return []

    async def fake_agent(inp: AgentRunInput) -> AgentRunOutput:
        return AgentRunOutput(success=True, items_researched=0)

    await run_for_date(
        date(2026, 5, 15), db=db, config=runner_cfg,
        run_agent=fake_agent, recent_ratings=spy_ratings,
    )
    # 7-day inclusive window ending on target_date.
    assert seen["end"] == date(2026, 5, 15)
    assert seen["start"] == date(2026, 5, 9)


@pytest.mark.asyncio
async def test_notes_clobber_marks_failed(
    db_path: Path, runner_cfg: RunnerConfig, vault_root: Path,
) -> None:
    db = NewsResearchStateDB(db_path)
    await db.init_db()
    master = _seed_master(vault_root, date(2026, 5, 15))

    async def clobbering_agent(inp: AgentRunInput) -> AgentRunOutput:
        master.write_text(
            master.read_text().replace("<!-- human -->", "AGENT WUZ HERE")
        )
        return AgentRunOutput(success=True, items_researched=1)

    await run_for_date(
        date(2026, 5, 15), db=db, config=runner_cfg,
        run_agent=clobbering_agent, recent_ratings=_no_ratings,
    )

    row = await db.get_run(date(2026, 5, 15))
    assert row["status"] == "failed_notes_clobbered"


@pytest.mark.asyncio
async def test_agent_failure_marks_failed_no_raise(
    db_path: Path, runner_cfg: RunnerConfig, vault_root: Path,
) -> None:
    db = NewsResearchStateDB(db_path)
    await db.init_db()
    _seed_master(vault_root, date(2026, 5, 15))

    async def boom(inp: AgentRunInput) -> AgentRunOutput:
        raise RuntimeError("agent exploded")

    # Must NOT raise — research is best-effort.
    await run_for_date(
        date(2026, 5, 15), db=db, config=runner_cfg,
        run_agent=boom, recent_ratings=_no_ratings,
    )

    row = await db.get_run(date(2026, 5, 15))
    assert row["status"] == "failed"
    assert "agent exploded" in row["error"]


@pytest.mark.asyncio
async def test_agent_reports_failure_marks_failed(
    db_path: Path, runner_cfg: RunnerConfig, vault_root: Path,
) -> None:
    db = NewsResearchStateDB(db_path)
    await db.init_db()
    _seed_master(vault_root, date(2026, 5, 15))

    async def fail(inp: AgentRunInput) -> AgentRunOutput:
        return AgentRunOutput(success=False, error="mcp unavailable")

    await run_for_date(
        date(2026, 5, 15), db=db, config=runner_cfg,
        run_agent=fail, recent_ratings=_no_ratings,
    )
    row = await db.get_run(date(2026, 5, 15))
    assert row["status"] == "failed"
    assert row["error"] == "mcp unavailable"
