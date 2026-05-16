"""Integration tests for the news_research activation wiring inside
`_run_news_daily_master_path`.

`test_orchestrator_news_research.py` exercises only the pure
`_build_news_chain_fn` with hand-written stubs. The *real* assembly in
`_run_news_daily_master_path` (importing news_research.runner/state, building
`NewsResearchStateDB(...).init_db()`, `ResearchRunnerConfig`, the
`_recent_ratings` provider-selection closure, `research_run_for_date_fn`, and
composing it via `_build_news_chain_fn`) had ZERO executing coverage
(pr-test-analyzer C1). These tests run the function end-to-end with the
scheduler patched so the wiring actually executes.

Seam / patch strategy
---------------------
`_run_news_daily_master_path` ends in `await sched.run_forever()` which loops
forever. The scheduler is imported *locally* inside the function as
`from .news_daily_master.scheduler import NewsDailyScheduler`, so patching
`automation_daemon.news_daily_master.scheduler.NewsDailyScheduler` with a fake that
(a) records the `run_for_date_fn` kwarg and (b) has an async no-op
`run_forever` lets the function return immediately while letting us drive the
captured chain ourselves.

The three stage entrypoints are imported locally too, so they are patched at
their *source* modules:
  - `automation_daemon.news_daily_master.runner.run_for_date`
  - `automation_daemon.news_research.runner.run_for_date`
  - `automation_daemon.news_personal_digest.runner.run_for_date`

`_master_completed` reads the real `NewsDailyMasterStateDB.get_run(d)` and
requires `status == "completed"`. To let the chain proceed, the patched master
`run_for_date` mock writes a real `completed` row into the same state DB
(`config.email_ingest_state_db_path`) via `insert_run` + `update_run`. This
keeps the gate behaviour real rather than mocking `db.get_run`.

`send_message` is patched as an AsyncMock safety net so the `notify` closure
can never hit Telegram even if a path tries to notify.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from automation_daemon.config import DaemonConfig
from automation_daemon.news_daily_master.state import NewsDailyMasterStateDB


def _make_config(tmp_path, **overrides) -> DaemonConfig:
    base = dict(
        telegram_bot_token="tok",
        telegram_chat_id="chat",
        pkm_vault_path=tmp_path,
        email_ingest_enabled=False,
        news_ingest_enabled=False,
        email_ingest_state_db_path=tmp_path / "state.db",
        news_daily_master_enabled=True,
        news_daily_telegram_topic_id=99,
    )
    base.update(overrides)
    return DaemonConfig(**base)


class _FakeScheduler:
    """Captures the run_for_date_fn it is built with; run_forever is a no-op
    so `_run_news_daily_master_path` returns instead of looping forever."""

    captured: dict = {}

    def __init__(self, *, db, run_for_date_fn, notify, fire_time, tz,
                 backfill_window_days):
        type(self).captured = {
            "db": db,
            "run_for_date_fn": run_for_date_fn,
            "notify": notify,
            "fire_time": fire_time,
            "tz": tz,
            "backfill_window_days": backfill_window_days,
        }

    async def run_forever(self) -> None:  # async no-op — returns immediately
        return None


async def _complete_master_row(
    state_db_path, d: date, status: str = "completed",
) -> None:
    """Write a real terminal master row so `_master_completed` (which reads
    the real `NewsDailyMasterStateDB.get_run`) lets the chain proceed.

    `status` defaults to "completed"; pass "completed_with_skips" to exercise
    the skip-gate path."""
    mdb = NewsDailyMasterStateDB(state_db_path)
    await mdb.init_db()
    await mdb.insert_run(d)
    await mdb.update_run(d, status)


@pytest.mark.asyncio
async def test_wiring_research_enabled_digest_enabled_composes_master_research_digest(
    tmp_path,
):
    """research + digest both enabled: the wiring assembles a chain that drives
    master -> research -> digest in order. Pins the
    `if config.news_research_enabled:` block + `_build_news_chain_fn` compose
    at the bottom of `_run_news_daily_master_path`."""
    config = _make_config(
        tmp_path,
        news_personal_digest_enabled=True,
        news_research_enabled=True,
    )
    calls: list[str] = []
    d = date(2026, 5, 15)

    async def fake_master(target_date, **kwargs):
        calls.append("master")
        # Make _master_completed's real db.get_run return "completed".
        await _complete_master_row(config.email_ingest_state_db_path,
                                   target_date)

    async def fake_research(target_date, **kwargs):
        calls.append("research")

    async def fake_digest(target_date, **kwargs):
        calls.append("digest")

    with patch(
        "automation_daemon.news_daily_master.scheduler.NewsDailyScheduler",
        _FakeScheduler,
    ), patch(
        "automation_daemon.news_daily_master.runner.run_for_date",
        AsyncMock(side_effect=fake_master),
    ), patch(
        "automation_daemon.news_research.runner.run_for_date",
        AsyncMock(side_effect=fake_research),
    ), patch(
        "automation_daemon.news_personal_digest.runner.run_for_date",
        AsyncMock(side_effect=fake_digest),
    ), patch(
        "automation_daemon.orchestrator.send_message", AsyncMock(),
    ):
        from automation_daemon.orchestrator import _run_news_daily_master_path

        # Must return (not hang) because run_forever is a no-op.
        await _run_news_daily_master_path(config)

    captured_fn = _FakeScheduler.captured.get("run_for_date_fn")
    assert captured_fn is not None, "scheduler was never constructed"

    # Drive the composed chain for a date and assert real order.
    await captured_fn(d)
    assert calls == ["master", "research", "digest"]


@pytest.mark.asyncio
async def test_wiring_research_enabled_digest_disabled_uses_empty_ratings_fallback(
    tmp_path,
):
    """research enabled, digest disabled (digest_db is None): the wiring still
    builds, the chain runs master -> research (no digest), and the injected
    `_recent_ratings` provider passed into news_research.run_for_date returns
    `[]`. Pins the `if digest_db is not None: ... else:` provider-selection
    branch and the news_research decoupling claim."""
    config = _make_config(
        tmp_path,
        news_personal_digest_enabled=False,
        news_research_enabled=True,
    )
    calls: list[str] = []
    captured_recent_ratings: list = []
    d = date(2026, 5, 15)

    async def fake_master(target_date, **kwargs):
        calls.append("master")
        await _complete_master_row(config.email_ingest_state_db_path,
                                   target_date)

    async def fake_research(target_date, **kwargs):
        calls.append("research")
        # Capture the recent_ratings provider the wiring injected.
        captured_recent_ratings.append(kwargs["recent_ratings"])

    async def fake_digest(target_date, **kwargs):
        calls.append("digest")

    with patch(
        "automation_daemon.news_daily_master.scheduler.NewsDailyScheduler",
        _FakeScheduler,
    ), patch(
        "automation_daemon.news_daily_master.runner.run_for_date",
        AsyncMock(side_effect=fake_master),
    ), patch(
        "automation_daemon.news_research.runner.run_for_date",
        AsyncMock(side_effect=fake_research),
    ), patch(
        "automation_daemon.news_personal_digest.runner.run_for_date",
        AsyncMock(side_effect=fake_digest),
    ), patch(
        "automation_daemon.orchestrator.send_message", AsyncMock(),
    ):
        from automation_daemon.orchestrator import _run_news_daily_master_path

        await _run_news_daily_master_path(config)

    captured_fn = _FakeScheduler.captured.get("run_for_date_fn")
    assert captured_fn is not None

    await captured_fn(d)
    assert calls == ["master", "research"]  # no digest stage

    # The empty-list fallback provider must have been injected and must
    # return [] over a sample window — pins news_research decoupling.
    assert len(captured_recent_ratings) == 1
    provider = captured_recent_ratings[0]
    result = await provider(date(2026, 5, 8), date(2026, 5, 15))
    assert result == []


@pytest.mark.asyncio
async def test_wiring_research_disabled_is_inert(tmp_path):
    """research disabled, digest enabled: the composed chain runs only
    master -> digest; the news_research entrypoint is never called. Proves the
    feature is inert by default / behaviourally equivalent when the flag is
    off."""
    config = _make_config(
        tmp_path,
        news_personal_digest_enabled=True,
        news_research_enabled=False,
    )
    calls: list[str] = []
    d = date(2026, 5, 15)

    async def fake_master(target_date, **kwargs):
        calls.append("master")
        await _complete_master_row(config.email_ingest_state_db_path,
                                   target_date)

    async def fake_research(target_date, **kwargs):
        calls.append("research")

    async def fake_digest(target_date, **kwargs):
        calls.append("digest")

    research_mock = AsyncMock(side_effect=fake_research)

    with patch(
        "automation_daemon.news_daily_master.scheduler.NewsDailyScheduler",
        _FakeScheduler,
    ), patch(
        "automation_daemon.news_daily_master.runner.run_for_date",
        AsyncMock(side_effect=fake_master),
    ), patch(
        "automation_daemon.news_research.runner.run_for_date",
        research_mock,
    ), patch(
        "automation_daemon.news_personal_digest.runner.run_for_date",
        AsyncMock(side_effect=fake_digest),
    ), patch(
        "automation_daemon.orchestrator.send_message", AsyncMock(),
    ):
        from automation_daemon.orchestrator import _run_news_daily_master_path

        await _run_news_daily_master_path(config)

    captured_fn = _FakeScheduler.captured.get("run_for_date_fn")
    assert captured_fn is not None

    await captured_fn(d)
    assert calls == ["master", "digest"]
    research_mock.assert_not_called()


@pytest.mark.asyncio
async def test_wiring_completed_with_skips_still_runs_research_and_digest(
    tmp_path,
):
    """A master run that ends in completed_with_skips must NOT gate the
    chain — research + digest still run (this is the skip-gate bug fix)."""
    config = _make_config(
        tmp_path,
        news_personal_digest_enabled=True,
        news_research_enabled=True,
    )
    calls: list[str] = []
    d = date(2026, 5, 15)

    async def fake_master(target_date, **kwargs):
        calls.append("master")
        await _complete_master_row(
            config.email_ingest_state_db_path, target_date,
            "completed_with_skips",
        )

    async def fake_research(target_date, **kwargs):
        calls.append("research")

    async def fake_digest(target_date, **kwargs):
        calls.append("digest")

    with patch(
        "automation_daemon.news_daily_master.scheduler.NewsDailyScheduler",
        _FakeScheduler,
    ), patch(
        "automation_daemon.news_daily_master.runner.run_for_date",
        AsyncMock(side_effect=fake_master),
    ), patch(
        "automation_daemon.news_research.runner.run_for_date",
        AsyncMock(side_effect=fake_research),
    ), patch(
        "automation_daemon.news_personal_digest.runner.run_for_date",
        AsyncMock(side_effect=fake_digest),
    ), patch(
        "automation_daemon.orchestrator.send_message", AsyncMock(),
    ):
        from automation_daemon.orchestrator import _run_news_daily_master_path

        await _run_news_daily_master_path(config)

    captured_fn = _FakeScheduler.captured.get("run_for_date_fn")
    assert captured_fn is not None, "scheduler was never constructed"

    await captured_fn(d)
    assert calls == ["master", "research", "digest"]
