from __future__ import annotations

from datetime import date

from automation_daemon.news_weekly_patterns.recurrence import (
    RecurringThread,
    rank_recurring_threads,
)

_DAY1 = """---
type: news-daily-master
target_date: 2026-05-11
categories: [AI, Tech]
---
# News Daily Master — 2026-05-11

## AI
- **[Title A](https://x/a)** — [[00-Inbox/news/2026-05-11/a-1|source]] — [[01-Projects/News/entities/Anthropic|Anthropic]] ships [[01-Projects/News/entities/Claude Code|Claude Code]] update.

## Tech
- **[Title B](https://x/b)** — [[00-Inbox/news/2026-05-11/b-2|source]] — [[01-Projects/News/entities/Google|Google]] news.
"""

_DAY2 = """---
type: news-daily-master
target_date: 2026-05-12
categories: [AI]
---
# News Daily Master — 2026-05-12

## AI
- **[Title C](https://x/c)** — [[00-Inbox/news/2026-05-12/c-3|source]] — [[01-Projects/News/entities/Anthropic|Anthropic]] again, and [[01-Projects/News/entities/Anthropic|Anthropic]] twice same day.
"""


def test_entity_link_parsed_excluding_source_links() -> None:
    threads = rank_recurring_threads(
        {date(2026, 5, 11): _DAY1, date(2026, 5, 12): _DAY2},
        recurrence_threshold=2,
    )
    names = {t.name for t in threads}
    assert "Anthropic" in names
    assert not any("Inbox" in n or n == "source" for n in names)


def test_distinct_day_count_dedupes_same_day_repeats() -> None:
    threads = rank_recurring_threads(
        {date(2026, 5, 11): _DAY1, date(2026, 5, 12): _DAY2},
        recurrence_threshold=2,
    )
    anthropic = next(t for t in threads if t.name == "Anthropic")
    assert anthropic.day_count == 2
    assert anthropic.dates == [date(2026, 5, 11), date(2026, 5, 12)]


def test_threshold_filters_out_single_day_entities() -> None:
    threads = rank_recurring_threads(
        {date(2026, 5, 11): _DAY1, date(2026, 5, 12): _DAY2},
        recurrence_threshold=2,
    )
    names = {t.name for t in threads}
    assert "Google" not in names


def test_ranked_descending_by_day_count_then_name() -> None:
    day3 = (
        "## AI\n- x [[01-Projects/News/entities/Google|Google]] "
        "[[01-Projects/News/entities/Anthropic|Anthropic]]\n"
    )
    threads = rank_recurring_threads(
        {
            date(2026, 5, 11): _DAY1,
            date(2026, 5, 12): _DAY2,
            date(2026, 5, 13): day3,
        },
        recurrence_threshold=2,
    )
    assert [(t.name, t.day_count) for t in threads] == [
        ("Anthropic", 3), ("Google", 2),
    ]


def test_categories_collected_per_thread() -> None:
    threads = rank_recurring_threads(
        {date(2026, 5, 11): _DAY1, date(2026, 5, 12): _DAY2},
        recurrence_threshold=2,
    )
    anthropic = next(t for t in threads if t.name == "Anthropic")
    assert "AI" in anthropic.categories


def test_empty_input_returns_empty() -> None:
    assert rank_recurring_threads({}, recurrence_threshold=3) == []


def test_malformed_wikilink_ignored() -> None:
    bad = "## AI\n- [[01-Projects/News/entities/Unclosed|Unclosed\n"
    threads = rank_recurring_threads(
        {date(2026, 5, 11): bad, date(2026, 5, 12): bad},
        recurrence_threshold=2,
    )
    assert threads == []
