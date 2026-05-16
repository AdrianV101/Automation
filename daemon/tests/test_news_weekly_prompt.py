from __future__ import annotations

from datetime import date

from automation_daemon.news_weekly_patterns.prompt import (
    build_runner_prompt,
    render_recurrence_block,
)
from automation_daemon.news_weekly_patterns.recurrence import RecurringThread


def test_render_recurrence_block_lists_threads() -> None:
    threads = [
        RecurringThread("Anthropic", 5, [date(2026, 5, 11)], ["AI"]),
        RecurringThread("Google", 3, [date(2026, 5, 12)], ["Tech"]),
    ]
    block = render_recurrence_block(threads=threads)
    assert "Anthropic" in block
    assert "5" in block
    assert "AI" in block


def test_render_recurrence_block_empty() -> None:
    block = render_recurrence_block(threads=[])
    assert "no recurring" in block.lower()


def test_build_runner_prompt_has_week_and_dates_and_caps() -> None:
    threads = [RecurringThread("Anthropic", 5, [date(2026, 5, 11)], ["AI"])]
    prompt = build_runner_prompt(
        iso_week="2026-20",
        week_dates=[date(2026, 5, 11), date(2026, 5, 17)],
        recurrence_block=render_recurrence_block(threads=threads),
        ratings_block="## Recent ratings\n\n(no recent ratings)\n",
        max_threads=8,
    )
    assert "2026-20" in prompt
    assert "01-Projects/News/weekly/2026-20.md" in prompt
    assert "2026-05-11" in prompt and "2026-05-17" in prompt
    assert "8" in prompt
    assert "news-weekly-patterns skill" in prompt
