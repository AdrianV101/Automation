from __future__ import annotations

from datetime import date
from typing import Any, Iterable

_RATING_ORDER = ("star", "thumbs_up", "thumbs_down")
_RATING_GLYPH = {"star": "⭐", "thumbs_up": "👍", "thumbs_down": "👎"}


def render_ratings_block(*, ratings: Iterable[dict[str, Any]]) -> str:
    """Render recent rating rows into the research prompt's signal section.

    Mirrors the digest's feedback block shape (⭐ first, then 👍, then 👎)
    but framed as 'what the user found interesting' rather than 'ranking
    signal' — the research agent uses it to judge which items deserve a
    deep dive. Rows are typically `NewsDigestStateDB.recent_ratings`
    output (rated_at DESC) but may be empty when the digest is disabled.
    """
    rated = list(ratings)
    if not rated:
        return "## Recent ratings\n\n(no recent ratings)\n"

    by_rating: dict[str, list[dict[str, Any]]] = {k: [] for k in _RATING_ORDER}
    for row in rated:
        if row["rating"] in by_rating:
            by_rating[row["rating"]].append(row)

    parts = ["## Recent ratings", ""]
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
        "These are items the user reacted to recently. Treat ⭐ as the "
        "strongest signal of what is worth deep research; 👎 topics are "
        "rarely worth a deep dive.",
    )
    return "\n".join(parts) + "\n"


def build_runner_prompt(
    *, target_date: date, ratings_block: str, max_items: int,
) -> str:
    """Thin caller-side prompt that hands off to the news-research skill."""
    iso = target_date.isoformat()
    return (
        f"Deep-research the most interesting items for target_date={iso}.\n\n"
        f"- Master document: 01-Projects/News/daily/{iso}-master.md\n"
        f"- Interests profile: 01-Projects/News/interests-profile.md\n\n"
        f"{ratings_block}\n"
        f"Select at most {max_items} items by judgment and enrich them "
        f"in place. Use the news-research skill to perform the work and "
        f"return the structured JSON summary as your final action."
    )
