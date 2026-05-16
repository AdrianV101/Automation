from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from automation_daemon.news_weekly_patterns.runner import (
    AgentRunInput,
    AgentRunOutput,
    RunnerConfig,
    run_for_iso_week,
)
from automation_daemon.news_weekly_patterns.state import NewsWeeklyStateDB


def _write_master(root: Path, d: date, body: str) -> None:
    p = root / "01-Projects" / "News" / "daily"
    p.mkdir(parents=True, exist_ok=True)
    (p / f"{d.isoformat()}-master.md").write_text(body)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    return tmp_path / "vault"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state.db"


def test_agent_run_output_pair_invariant() -> None:
    with pytest.raises(ValueError):
        AgentRunOutput(success=True, error="x")
    with pytest.raises(ValueError):
        AgentRunOutput(success=False, error=None)


@pytest.mark.asyncio
async def test_insufficient_corpus_skips_without_agent(
    vault: Path, db_path: Path,
) -> None:
    _write_master(vault, date(2026, 5, 11), "## AI\n- x\n")
    _write_master(vault, date(2026, 5, 12), "## AI\n- y\n")
    db = NewsWeeklyStateDB(db_path)
    await db.init_db()
    called = False

    async def run_agent(_: AgentRunInput) -> AgentRunOutput:
        nonlocal called
        called = True
        return AgentRunOutput(success=True)

    pinged: list[str] = []

    async def notify(msg: str) -> None:
        pinged.append(msg)

    await run_for_iso_week(
        "2026-20",
        db=db,
        config=RunnerConfig(vault_root=vault, min_days=3),
        run_agent=run_agent,
        recent_ratings=_no_ratings,
        notify=notify,
    )
    row = await db.get_run("2026-20")
    assert row["status"] == "skipped_insufficient_corpus"
    assert called is False
    assert pinged == []


@pytest.mark.asyncio
async def test_success_completes_and_pings(
    vault: Path, db_path: Path,
) -> None:
    for day in range(11, 18):
        _write_master(
            vault, date(2026, 5, day),
            "## AI\n- [[01-Projects/News/entities/Anthropic|Anthropic]]\n",
        )
    db = NewsWeeklyStateDB(db_path)
    await db.init_db()

    async def run_agent(inp: AgentRunInput) -> AgentRunOutput:
        assert inp.iso_week == "2026-20"
        assert "Anthropic" in inp.prompt
        return AgentRunOutput(
            success=True, threads_written=1, entities_enriched=1,
            cost_usd=0.12, turns_used=9,
        )

    pinged: list[str] = []

    async def notify(msg: str) -> None:
        pinged.append(msg)

    await run_for_iso_week(
        "2026-20",
        db=db,
        config=RunnerConfig(vault_root=vault, min_days=3),
        run_agent=run_agent,
        recent_ratings=_no_ratings,
        notify=notify,
    )
    row = await db.get_run("2026-20")
    assert row["status"] == "completed"
    assert row["threads_written"] == 1
    assert len(pinged) == 1
    assert "2026-20" in pinged[0]


@pytest.mark.asyncio
async def test_agent_failure_marks_failed_no_ping(
    vault: Path, db_path: Path,
) -> None:
    for day in range(11, 18):
        _write_master(vault, date(2026, 5, day), "## AI\n- x\n")
    db = NewsWeeklyStateDB(db_path)
    await db.init_db()

    async def run_agent(_: AgentRunInput) -> AgentRunOutput:
        raise RuntimeError("agent boom")

    pinged: list[str] = []

    async def notify(msg: str) -> None:
        pinged.append(msg)

    await run_for_iso_week(
        "2026-20",
        db=db,
        config=RunnerConfig(
            vault_root=vault, min_days=3,
            retry_backoff_seconds=(0.0,),
        ),
        run_agent=run_agent,
        recent_ratings=_no_ratings,
        notify=notify,
    )
    row = await db.get_run("2026-20")
    assert row["status"] == "failed"
    assert pinged == []


async def _no_ratings(start: date, end: date) -> list[dict]:
    return []
