from __future__ import annotations

import inspect

from automation_daemon import orchestrator


def test_weekly_path_function_exists() -> None:
    assert hasattr(orchestrator, "_run_news_weekly_patterns_path")


def test_run_daemon_enables_on_news_weekly_flag() -> None:
    src = inspect.getsource(orchestrator.run_daemon)
    assert "news_weekly_enabled" in src
    assert "news-weekly-patterns" in src
    # both the early-exit OR-guard and the task-create reference the flag
    assert src.count("news_weekly_enabled") >= 2


def test_weekly_path_builds_independent_scheduler() -> None:
    src = inspect.getsource(
        orchestrator._run_news_weekly_patterns_path,
    )
    assert "NewsWeeklyScheduler" in src
    assert "run_for_iso_week" in src
    assert "run_agent_via_agent_infra" in src
    assert "_build_news_chain_fn" not in src
