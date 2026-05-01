from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from news_pipeline import NewsItem
from audio_ingest.hacker_news_adapter import _to_news_item

FIXTURES = Path(__file__).parent / "fixtures" / "hn"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_story_url_maps_canonical_fields():
    item = _to_news_item(_load("story_url.json"))
    assert isinstance(item, NewsItem)
    assert item.message_id == "hn-42000001"
    assert item.source == "Hacker News"
    assert item.source_type == "hacker-news"
    assert item.source_address == "https://news.ycombinator.com/item?id=42000001"
    assert item.subject == "Show HN: A new way to do X"
    assert item.received_at == datetime.fromtimestamp(1746086400, tz=timezone.utc)


def test_story_url_extras_carry_hn_metadata():
    item = _to_news_item(_load("story_url.json"))
    assert item.extra == {
        "hn-id": 42000001,
        "hn-points": 350,
        "hn-comments": 87,
        "hn-author": "alice",
    }


def test_story_url_body_includes_external_link():
    item = _to_news_item(_load("story_url.json"))
    assert "[Show HN: A new way to do X](https://example.com/foo)" in item.body_md
    assert "**Points:** 350" in item.body_md
    assert "[87 on HN](https://news.ycombinator.com/item?id=42000001)" in item.body_md
    assert "@alice" in item.body_md


def test_ask_hn_body_prepends_html_text_and_omits_external_link():
    item = _to_news_item(_load("ask_hn.json"))
    assert "I'm wondering how others approach *Y*" in item.body_md
    # no external URL line because Ask HN has no `url`:
    assert "https://example.com" not in item.body_md
    # but the comments line + author still appear:
    assert "**Points:** 210" in item.body_md
    assert "[145 on HN](https://news.ycombinator.com/item?id=42000002)" in item.body_md
    assert "@bob" in item.body_md


def test_show_hn_with_text_renders_text_then_external_link():
    item = _to_news_item(_load("show_hn.json"))
    assert "Built this in 12 hours" in item.body_md
    assert "[Show HN: My weekend project](https://example.com/project)" in item.body_md
    assert "**Points:** 540" in item.body_md


def test_extras_dont_collide_with_reserved_frontmatter_keys():
    """NewsItem raises if extra keys would shadow canonical frontmatter fields.
    Sentinel test: the conversion must never produce e.g. extra={'subject': ...}.
    """
    for fixture in ("story_url.json", "ask_hn.json", "show_hn.json"):
        # Construction would raise inside NewsItem.__post_init__ on collision.
        _to_news_item(_load(fixture))


def test_received_at_always_tz_aware():
    for fixture in ("story_url.json", "ask_hn.json", "show_hn.json"):
        item = _to_news_item(_load(fixture))
        assert item.received_at.tzinfo is not None


def test_missing_score_defaults_to_zero():
    """Brand-new items can briefly have null scores in the API."""
    item = _to_news_item({
        "id": 1, "type": "story", "by": "x", "time": 1746086400,
        "title": "t", "url": "https://e.com",
    })
    assert item.extra["hn-points"] == 0


def test_missing_descendants_defaults_to_zero():
    item = _to_news_item({
        "id": 1, "type": "story", "by": "x", "time": 1746086400,
        "title": "t", "score": 5, "url": "https://e.com",
    })
    assert item.extra["hn-comments"] == 0
