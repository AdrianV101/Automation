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


@pytest.mark.asyncio
async def test_agent_success_false_marks_failed_no_ping(
    vault: Path, db_path: Path,
) -> None:
    for day in range(11, 18):
        _write_master(vault, date(2026, 5, day), "## AI\n- x\n")
    db = NewsWeeklyStateDB(db_path)
    await db.init_db()

    async def run_agent(_: AgentRunInput) -> AgentRunOutput:
        return AgentRunOutput(success=False, error="agent said no")

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
    assert row["error"] == "agent said no"
    assert pinged == []


@pytest.mark.asyncio
async def test_ratings_provider_exception_is_non_fatal(
    vault: Path, db_path: Path,
) -> None:
    for day in range(11, 18):
        _write_master(
            vault, date(2026, 5, day),
            "## AI\n- [[01-Projects/News/entities/Anthropic|Anthropic]]\n",
        )
    db = NewsWeeklyStateDB(db_path)
    await db.init_db()

    async def bad_ratings(start: date, end: date) -> list[dict]:
        raise RuntimeError("ratings db down")

    async def run_agent(inp: AgentRunInput) -> AgentRunOutput:
        return AgentRunOutput(success=True, threads_written=1)

    pinged: list[str] = []

    async def notify(msg: str) -> None:
        pinged.append(msg)

    await run_for_iso_week(
        "2026-20",
        db=db,
        config=RunnerConfig(vault_root=vault, min_days=3),
        run_agent=run_agent,
        recent_ratings=bad_ratings,
        notify=notify,
    )
    row = await db.get_run("2026-20")
    assert row["status"] == "completed"  # ratings failure did not abort
    assert len(pinged) == 1


@pytest.mark.asyncio
async def test_retry_succeeds_on_second_attempt(
    vault: Path, db_path: Path,
) -> None:
    for day in range(11, 18):
        _write_master(vault, date(2026, 5, day), "## AI\n- x\n")
    db = NewsWeeklyStateDB(db_path)
    await db.init_db()
    calls: list[int] = []

    async def run_agent(_: AgentRunInput) -> AgentRunOutput:
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError("transient")
        return AgentRunOutput(success=True, threads_written=1)

    pinged: list[str] = []

    async def notify(m: str) -> None:
        pinged.append(m)

    await run_for_iso_week(
        "2026-20", db=db,
        config=RunnerConfig(
            vault_root=vault, min_days=3,
            retry_backoff_seconds=(0.0, 0.0),
        ),
        run_agent=run_agent, recent_ratings=_no_ratings, notify=notify,
    )
    assert len(calls) == 2
    assert (await db.get_run("2026-20"))["status"] == "completed"
    assert len(pinged) == 1


@pytest.mark.asyncio
async def test_ping_failure_is_non_fatal(
    vault: Path, db_path: Path,
) -> None:
    for day in range(11, 18):
        _write_master(vault, date(2026, 5, day), "## AI\n- x\n")
    db = NewsWeeklyStateDB(db_path)
    await db.init_db()

    async def run_agent(_: AgentRunInput) -> AgentRunOutput:
        return AgentRunOutput(success=True, threads_written=1)

    async def bad_notify(_: str) -> None:
        raise RuntimeError("telegram down")

    await run_for_iso_week(
        "2026-20", db=db,
        config=RunnerConfig(vault_root=vault, min_days=3),
        run_agent=run_agent, recent_ratings=_no_ratings,
        notify=bad_notify,
    )
    assert (await db.get_run("2026-20"))["status"] == "completed"


def test_parse_weekly_summary_no_json_block() -> None:
    from automation_daemon.news_weekly_patterns.runner import (
        _parse_agent_summary,
    )
    out = _parse_agent_summary(["prose only"], turns_used=3, cost_usd=None)
    assert not out.success
    assert "no JSON summary block" in out.error


def test_parse_weekly_summary_invalid_json() -> None:
    from automation_daemon.news_weekly_patterns.runner import (
        _parse_agent_summary,
    )
    out = _parse_agent_summary(
        ["```json\n{bad}\n```"], turns_used=3, cost_usd=0.1,
    )
    assert not out.success
    assert "invalid JSON" in out.error


def test_parse_weekly_summary_success_false_no_error_field() -> None:
    from automation_daemon.news_weekly_patterns.runner import (
        _parse_agent_summary,
    )
    out = _parse_agent_summary(
        ['```json\n{"success": false}\n```'], turns_used=5, cost_usd=0.0,
    )
    assert not out.success
    assert out.error


def test_parse_weekly_summary_picks_last_json_block() -> None:
    from automation_daemon.news_weekly_patterns.runner import (
        _parse_agent_summary,
    )
    text = (
        '```json\n{"success": false, "error": "first"}\n```\n'
        '```json\n{"success": true, "threads_written": 2}\n```'
    )
    out = _parse_agent_summary([text], turns_used=10, cost_usd=0.2)
    assert out.success
    assert out.threads_written == 2


@pytest.mark.asyncio
async def test_clobber_check_unreadable_after_does_not_false_fail(
    vault: Path, db_path: Path, monkeypatch: "pytest.MonkeyPatch",
) -> None:
    # If the post-run notes-hash read fails (returns None), the run must
    # NOT be marked failed_notes_clobbered — that would be a permanent
    # terminal failure from a transient I/O error.
    import automation_daemon.news_weekly_patterns.runner as _r
    for day in range(11, 18):
        _write_master(vault, date(2026, 5, day), "## AI\n- x\n")
    wk = vault / "01-Projects" / "News" / "weekly"
    wk.mkdir(parents=True, exist_ok=True)
    (wk / "2026-20.md").write_text("# b\n\n## Notes\n\norig\n")
    db = NewsWeeklyStateDB(db_path)
    await db.init_db()

    calls = {"n": 0}
    real = _r._hash_notes_section

    def flaky(path):  # first (pre) call real, second (post) call None
        calls["n"] += 1
        return real(path) if calls["n"] == 1 else None

    monkeypatch.setattr(_r, "_hash_notes_section", flaky)

    async def run_agent(_: AgentRunInput) -> AgentRunOutput:
        return AgentRunOutput(success=True, threads_written=1)

    async def notify(_: str) -> None:
        pass

    await run_for_iso_week(
        "2026-20", db=db,
        config=RunnerConfig(vault_root=vault, min_days=3),
        run_agent=run_agent, recent_ratings=_no_ratings, notify=notify,
    )
    row = await db.get_run("2026-20")
    assert row["status"] == "completed"  # NOT failed_notes_clobbered
