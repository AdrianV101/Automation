from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from audio_ingest.news_research.runner import (
    AgentRunInput,
    AgentRunOutput,
    RunnerConfig,
    _parse_agent_summary,
    run_agent_via_agent_infra,
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


class TestParseAgentSummary:
    def test_parses_last_json_block(self) -> None:
        out = _parse_agent_summary([
            'progress\n```json\n{"success": false}\n```\n',
            'done\n```json\n{"success": true, "items_researched": 4}\n```',
        ], turns_used=9, cost_usd=0.5)
        assert out.success is True
        assert out.items_researched == 4
        assert out.turns_used == 9
        assert out.cost_usd == 0.5
        assert out.error is None

    def test_no_json_block_is_failure(self) -> None:
        out = _parse_agent_summary(
            ["no json here"], turns_used=1, cost_usd=None,
        )
        assert out.success is False
        assert "no JSON summary" in out.error

    def test_success_false_synthesises_error(self) -> None:
        out = _parse_agent_summary(
            ['```json\n{"success": false}\n```'],
            turns_used=2, cost_usd=None,
        )
        assert out.success is False
        assert out.error  # non-None per pair invariant

    def test_success_true_drops_stray_error(self) -> None:
        out = _parse_agent_summary(
            ['```json\n{"success": true, "error": "ignore me"}\n```'],
            turns_used=2, cost_usd=None,
        )
        assert out.success is True
        assert out.error is None

    def test_invalid_json_block_is_failure(self) -> None:
        out = _parse_agent_summary(
            ['```json\n{bad: json,}\n```'],
            turns_used=3, cost_usd=0.1,
        )
        assert out.success is False
        assert "invalid JSON summary" in out.error
        assert out.turns_used == 3
        assert out.cost_usd == 0.1


@pytest.mark.asyncio
async def test_run_agent_via_agent_infra_captures_cost() -> None:
    from agent_infra import AgentLoopResult
    from agent_infra.runner import TraceEvent

    async def fake_streaming(prompt, options, on_event=None):
        # Emit a completion trace carrying the cost, like the real runner.
        if on_event is not None:
            await on_event(TraceEvent(
                kind="complete", turns_used=7, cost_usd=0.27,
                files_written=[],
            ))
        return AgentLoopResult(
            text_parts=['```json\n{"success": true, '
                        '"items_researched": 2}\n```'],
            turns_used=7,
        )

    with patch(
        "audio_ingest.news_research.runner.run_agent_loop_streaming",
        side_effect=fake_streaming,
    ), patch(
        "audio_ingest.news_research.runner.build_agent_options",
        return_value=object(),
    ):
        out = await run_agent_via_agent_infra(AgentRunInput(
            target_date=date(2026, 5, 15),
            vault_root=Path("/tmp/vault"),
            model="claude-sonnet-4-6",
            max_items=3,
            max_turns=60,
            prompt="do research",
        ))

    assert out.success is True
    assert out.items_researched == 2
    assert out.turns_used == 7
    assert out.cost_usd == pytest.approx(0.27)


@pytest.mark.asyncio
async def test_run_agent_via_agent_infra_passes_max_turns() -> None:
    """build_agent_options must receive max_turns from AgentRunInput."""
    from unittest.mock import MagicMock
    from agent_infra import AgentLoopResult
    from agent_infra.runner import TraceEvent

    async def fake_streaming(prompt, options, on_event=None):
        if on_event is not None:
            await on_event(TraceEvent(
                kind="complete", turns_used=5, cost_usd=0.10,
                files_written=[],
            ))
        return AgentLoopResult(
            text_parts=['```json\n{"success": true, "items_researched": 1}\n```'],
            turns_used=5,
        )

    mock_build = MagicMock(return_value=object())
    with patch(
        "audio_ingest.news_research.runner.run_agent_loop_streaming",
        side_effect=fake_streaming,
    ), patch(
        "audio_ingest.news_research.runner.build_agent_options",
        mock_build,
    ):
        await run_agent_via_agent_infra(AgentRunInput(
            target_date=date(2026, 5, 15),
            vault_root=Path("/tmp/vault"),
            model="claude-sonnet-4-6",
            max_items=3,
            max_turns=42,
            prompt="do research",
        ))

    assert mock_build.call_args.kwargs["max_turns"] == 42


def test_runner_config_default_max_turns() -> None:
    """RunnerConfig must default max_turns to 60."""
    cfg = RunnerConfig(vault_root=Path("/tmp/vault"))
    assert cfg.max_turns == 60


def test_agent_run_input_carries_max_turns() -> None:
    """AgentRunInput must expose the max_turns field."""
    inp = AgentRunInput(
        target_date=date(2026, 5, 15),
        vault_root=Path("/tmp/vault"),
        model="claude-sonnet-4-6",
        max_items=3,
        max_turns=99,
        prompt="test",
    )
    assert inp.max_turns == 99


# ---------------------------------------------------------------------------
# Retry-loop behavioural tests (Change 2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_then_success_marks_completed(
    db_path: Path, runner_cfg: RunnerConfig, vault_root: Path,
) -> None:
    """Agent that raises on attempt 1, returns success on attempt 2."""
    db = NewsResearchStateDB(db_path)
    await db.init_db()
    _seed_master(vault_root, date(2026, 5, 15))

    call_count = 0

    async def flaky_agent(inp: AgentRunInput) -> AgentRunOutput:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient error")
        return AgentRunOutput(success=True, items_researched=1)

    cfg = RunnerConfig(
        vault_root=vault_root,
        retry_backoff_seconds=(0, 0),  # no real waits
    )
    await run_for_date(
        date(2026, 5, 15), db=db, config=cfg,
        run_agent=flaky_agent, recent_ratings=_no_ratings,
    )

    assert call_count == 2
    row = await db.get_run(date(2026, 5, 15))
    assert row["status"] == "completed"
    assert row["items_researched"] == 1


@pytest.mark.asyncio
async def test_retry_exhaustion_marks_failed_with_last_error(
    db_path: Path, vault_root: Path,
) -> None:
    """Agent that always raises exhausts all attempts and marks failed."""
    db = NewsResearchStateDB(db_path)
    await db.init_db()
    _seed_master(vault_root, date(2026, 5, 15))

    call_count = 0

    async def always_boom(inp: AgentRunInput) -> AgentRunOutput:
        nonlocal call_count
        call_count += 1
        raise RuntimeError(f"boom-{call_count}")

    cfg = RunnerConfig(
        vault_root=vault_root,
        retry_backoff_seconds=(0, 0),
    )
    # Must NOT raise — research is best-effort.
    await run_for_date(
        date(2026, 5, 15), db=db, config=cfg,
        run_agent=always_boom, recent_ratings=_no_ratings,
    )

    assert call_count == 2
    row = await db.get_run(date(2026, 5, 15))
    assert row["status"] == "failed"
    assert "agent_exception:" in row["error"]
    assert "boom-2" in row["error"]  # last attempt's message


@pytest.mark.asyncio
async def test_single_attempt_when_backoff_single(
    db_path: Path, vault_root: Path,
) -> None:
    """With a single-entry backoff tuple, only one attempt is made."""
    db = NewsResearchStateDB(db_path)
    await db.init_db()
    _seed_master(vault_root, date(2026, 5, 15))

    call_count = 0

    async def always_boom(inp: AgentRunInput) -> AgentRunOutput:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("boom")

    cfg = RunnerConfig(
        vault_root=vault_root,
        retry_backoff_seconds=(0,),
    )
    await run_for_date(
        date(2026, 5, 15), db=db, config=cfg,
        run_agent=always_boom, recent_ratings=_no_ratings,
    )

    assert call_count == 1
    row = await db.get_run(date(2026, 5, 15))
    assert row["status"] == "failed"


# ---------------------------------------------------------------------------
# Outer stuck-row handler test (Change 3 / I1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inner_unexpected_error_marked_failed_by_outer_handler(
    db_path: Path, vault_root: Path,
) -> None:
    """Patch _hash_notes_section to raise — it's called in _run_for_date_inner
    before the retry loop and is not wrapped by any inner except, so the
    exception propagates to the outer handler in run_for_date.
    """
    db = NewsResearchStateDB(db_path)
    await db.init_db()
    _seed_master(vault_root, date(2026, 5, 15))

    async def fake_agent(inp: AgentRunInput) -> AgentRunOutput:  # pragma: no cover
        return AgentRunOutput(success=True, items_researched=0)

    cfg = RunnerConfig(vault_root=vault_root, retry_backoff_seconds=(0,))

    with patch(
        "audio_ingest.news_research.runner._hash_notes_section",
        side_effect=RuntimeError("boom"),
    ):
        # Must NOT raise — outer handler catches and records.
        await run_for_date(
            date(2026, 5, 15), db=db, config=cfg,
            run_agent=fake_agent, recent_ratings=_no_ratings,
        )

    row = await db.get_run(date(2026, 5, 15))
    assert row["status"] == "failed"
    assert row["error"].startswith("unexpected:")
    assert "boom" in row["error"]


# ---------------------------------------------------------------------------
# Ratings-provider-failure fallback test (Change 4 / I2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ratings_provider_failure_degrades_gracefully(
    db_path: Path, vault_root: Path,
) -> None:
    """Ratings provider raising must not fail the research run."""
    db = NewsResearchStateDB(db_path)
    await db.init_db()
    _seed_master(vault_root, date(2026, 5, 15))

    call_count = 0

    async def fake_agent(inp: AgentRunInput) -> AgentRunOutput:
        nonlocal call_count
        call_count += 1
        return AgentRunOutput(success=True, items_researched=0)

    async def boom_ratings(start: date, end: date) -> list[dict]:
        raise RuntimeError("ratings DB unavailable")

    cfg = RunnerConfig(vault_root=vault_root, retry_backoff_seconds=(0,))
    await run_for_date(
        date(2026, 5, 15), db=db, config=cfg,
        run_agent=fake_agent, recent_ratings=boom_ratings,
    )

    row = await db.get_run(date(2026, 5, 15))
    assert row["status"] == "completed"  # ratings are optional
    assert call_count == 1  # research proceeded despite ratings failure


@pytest.mark.asyncio
async def test_run_agent_via_agent_infra_error_branch_keeps_cost() -> None:
    from agent_infra import AgentLoopResult
    from agent_infra.runner import TraceEvent

    async def fake_streaming(prompt, options, on_event=None):
        if on_event is not None:
            await on_event(TraceEvent(
                kind="complete", turns_used=5, cost_usd=0.19,
                files_written=[],
            ))
        return AgentLoopResult(
            text_parts=["partial output"],
            turns_used=5,
            error="Agent hit turn limit (5 turns)",
        )

    with patch(
        "audio_ingest.news_research.runner.run_agent_loop_streaming",
        side_effect=fake_streaming,
    ), patch(
        "audio_ingest.news_research.runner.build_agent_options",
        return_value=object(),
    ):
        out = await run_agent_via_agent_infra(AgentRunInput(
            target_date=date(2026, 5, 15),
            vault_root=Path("/tmp/vault"),
            model="claude-sonnet-4-6",
            max_items=3,
            max_turns=60,
            prompt="do research",
        ))

    assert out.success is False
    assert out.error == "Agent hit turn limit (5 turns)"
    assert out.cost_usd == pytest.approx(0.19)
    assert out.turns_used == 5
