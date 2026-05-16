from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from .recurrence import RecurringThread

_RATING_ORDER = ("star", "thumbs_up", "thumbs_down")
_RATING_GLYPH = {"star": "⭐", "thumbs_up": "👍", "thumbs_down": "👎"}


def render_ratings_block(*, ratings: Iterable[dict[str, Any]]) -> str:
    """Recent rating rows -> prompt signal section.

    Same shape as news_research/news_personal_digest (⭐, 👍, 👎). Rows are
    typically NewsDigestStateDB.recent_ratings output; may be empty.
    """
    rated = list(ratings)
    if not rated:
        return "## Recent ratings\n\n(no recent ratings)\n"
    by_rating: dict[str, list[dict[str, Any]]] = {
        k: [] for k in _RATING_ORDER
    }
    for row in rated:
        if row["rating"] in by_rating:
            by_rating[row["rating"]].append(row)
    parts = ["## Recent ratings", ""]
    for key in _RATING_ORDER:
        items = by_rating[key]
        if not items:
            continue
        parts.append(f"{_RATING_GLYPH[key]} ({len(items)} items)")
        for it in items:
            cat = (it.get("category") or "uncategorised").lower()
            title = it.get("title") or "(no title)"
            parts.append(f"- {title} (#{cat})")
        parts.append("")
    parts.append(
        "These are items the user reacted to recently. Weight ⭐/👍 "
        "threads as more worth surfacing; 👎 topics rarely are.",
    )
    return "\n".join(parts) + "\n"


def render_recurrence_block(*, threads: Iterable[RecurringThread]) -> str:
    """Deterministic recurring-thread ranking -> prompt section.

    This is the BACKBONE the agent must use: it lists, in rank order,
    every entity that recurred on >= the threshold number of distinct
    days, with its day count, the dates, and the categories it appeared
    under. The agent writes narrative for these; it may add at most one
    extra qualitative thread it judges significant.
    """
    items = list(threads)
    if not items:
        return (
            "## Recurring threads (deterministic)\n\n"
            "(no recurring threads met the threshold this week)\n"
        )
    parts = ["## Recurring threads (deterministic, rank order)", ""]
    for t in items:
        dates = ", ".join(d.isoformat() for d in t.dates)
        cats = ", ".join(t.categories) if t.categories else "—"
        parts.append(
            f"- **{t.name}** — {t.day_count} distinct days "
            f"[{dates}] — categories: {cats}",
        )
    parts.append("")
    return "\n".join(parts) + "\n"


def build_runner_prompt(
    *,
    iso_week: str,
    week_dates: list[date],
    recurrence_block: str,
    ratings_block: str,
    max_threads: int,
) -> str:
    """Thin caller-side prompt that hands off to the news-weekly skill."""
    first = week_dates[0].isoformat()
    last = week_dates[-1].isoformat()
    master_paths = "\n".join(
        f"  - 01-Projects/News/daily/{d.isoformat()}-master.md"
        for d in week_dates
    )
    return (
        f"Produce the weekly pattern note for ISO week {iso_week} "
        f"(covering {first} .. {last}).\n\n"
        f"- Weekly note to write: "
        f"01-Projects/News/weekly/{iso_week}.md\n"
        f"- Daily masters for the week (some may not exist — skip those):\n"
        f"{master_paths}\n"
        f"- Interests profile: 01-Projects/News/interests-profile.md\n\n"
        f"{recurrence_block}\n"
        f"{ratings_block}\n"
        f"Write at most {max_threads} thread sections, driven by the "
        f"deterministic ranking above (you may add at most ONE extra "
        f"qualitative thread). Use the news-weekly-patterns skill to do "
        f"the work and return the structured JSON summary as your final "
        f"action."
    )
