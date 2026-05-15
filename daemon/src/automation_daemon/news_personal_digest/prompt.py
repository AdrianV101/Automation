from __future__ import annotations

from datetime import date
from typing import Any, Iterable

_RATING_ORDER = ("star", "thumbs_up", "thumbs_down")
_RATING_GLYPH = {"star": "⭐", "thumbs_up": "👍", "thumbs_down": "👎"}


def render_feedback_block(*, ratings: Iterable[dict[str, Any]]) -> str:
    """Format a list of joined rating rows into the prompt's feedback section.

    Groups by rating with ⭐ first, 👍 second, 👎 third. Within each group
    the order is whatever the caller passed (typically `recent_ratings`
    output, which is rated_at DESC). The block includes a category hashtag
    per item so the agent can connect topic patterns to its category model.
    """
    rated = list(ratings)
    if not rated:
        return "## Recent feedback (last 7 days)\n\n(no recent ratings)\n"

    by_rating: dict[str, list[dict[str, Any]]] = {k: [] for k in _RATING_ORDER}
    for row in rated:
        if row["rating"] in by_rating:
            by_rating[row["rating"]].append(row)

    parts = ["## Recent feedback (last 7 days)", ""]
    for key in _RATING_ORDER:
        items = by_rating[key]
        if not items:
            continue
        glyph = _RATING_GLYPH[key]
        parts.append(f"{glyph} ({len(items)} items)")
        for it in items:
            cat = (it.get("category") or "uncategorised").lower()
            title = it.get("title") or "(no title)"
            parts.append(f"- {title} (#{cat})")
        parts.append("")
    parts.append(
        "Use these as preference signal when ranking today's items. "
        "Treat ⭐ as a stronger positive signal than 👍.",
    )
    return "\n".join(parts) + "\n"


def build_runner_prompt(*, target_date: date, feedback_block: str) -> str:
    """Thin caller-side prompt that hands off to the news-personal-digest skill."""
    iso = target_date.isoformat()
    return (
        f"Generate the personalised news digest for target_date={iso}.\n\n"
        f"- Master document: 01-Projects/News/daily/{iso}-master.md\n"
        f"- Interests profile: 01-Projects/News/interests-profile.md\n\n"
        f"{feedback_block}\n"
        "Use the news-personal-digest skill to perform the work and return "
        "the structured JSON summary as your final action."
    )
