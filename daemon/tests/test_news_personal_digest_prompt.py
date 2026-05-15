"""Tests for news_personal_digest.prompt — pure functions."""
from __future__ import annotations

from datetime import date

from automation_daemon.news_personal_digest.prompt import (
    build_runner_prompt,
    render_feedback_block,
)


def test_build_runner_prompt_has_target_date_and_paths() -> None:
    out = build_runner_prompt(
        target_date=date(2026, 5, 9),
        feedback_block="## Recent feedback\n\n(none)\n",
    )
    assert "2026-05-09" in out
    assert "01-Projects/News/daily/2026-05-09-master.md" in out
    assert "01-Projects/News/interests-profile.md" in out
    assert "news-personal-digest" in out
    assert "## Recent feedback" in out


def test_render_feedback_block_empty_returns_none_marker() -> None:
    out = render_feedback_block(ratings=[])
    assert "## Recent feedback" in out
    assert "(no recent ratings)" in out


def test_render_feedback_block_groups_by_rating_priority() -> None:
    """Order: ⭐ first (strongest signal), then 👍, then 👎."""
    ratings = [
        {"rating": "thumbs_up",   "category": "Tech",     "title": "pgvector hits 50k stars"},
        {"rating": "thumbs_down", "category": "Finance",  "title": "Generic Series B"},
        {"rating": "star",        "category": "AI",       "title": "Anthropic 4.7"},
        {"rating": "star",        "category": "Finance",  "title": "ECB cut signal"},
        {"rating": "thumbs_up",   "category": "Personal", "title": "Spain residency"},
    ]
    out = render_feedback_block(ratings=ratings)
    star_idx = out.index("⭐")
    up_idx = out.index("👍")
    down_idx = out.index("👎")
    assert star_idx < up_idx < down_idx
    assert "Anthropic 4.7" in out
    assert "ECB cut signal" in out
    assert "(#ai)" in out.lower()
    assert "(#tech)" in out.lower()
