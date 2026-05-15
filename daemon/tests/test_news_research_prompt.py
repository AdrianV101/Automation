from __future__ import annotations

from datetime import date

from automation_daemon.news_research.prompt import (
    build_runner_prompt,
    render_ratings_block,
)


def test_render_ratings_block_empty() -> None:
    block = render_ratings_block(ratings=[])
    assert "no recent ratings" in block
    assert block.startswith("## Recent ratings")


def test_render_ratings_block_groups_by_rating() -> None:
    rows = [
        {"rating": "star", "category": "AI", "title": "Anthropic raises"},
        {"rating": "thumbs_down", "category": "Crypto", "title": "Memecoin"},
        {"rating": "star", "category": "Tech", "title": "New chip"},
    ]
    block = render_ratings_block(ratings=rows)
    assert "⭐ (2 items)" in block
    assert "👎 (1 items)" in block
    assert "- Anthropic raises (#ai)" in block
    # ⭐ group must appear before the 👎 group.
    assert block.index("⭐") < block.index("👎")


def test_build_runner_prompt_wires_paths_and_cap() -> None:
    p = build_runner_prompt(
        target_date=date(2026, 5, 15),
        ratings_block="## Recent ratings\n\n(no recent ratings)\n",
        max_items=3,
    )
    assert "2026-05-15" in p
    assert "01-Projects/News/daily/2026-05-15-master.md" in p
    assert "01-Projects/News/interests-profile.md" in p
    assert "at most 3" in p
    assert "news-research skill" in p
    assert "## Recent ratings" in p
