from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from news_pipeline import NewsItem, slugify_subject, write_news_item


def _newsletter(**overrides) -> NewsItem:
    base = dict(
        message_id="abc123@example.com",
        source="Example Newsletter",
        source_type="newsletter",
        source_address="newsletter@example.com",
        subject="Weekly Roundup #42",
        received_at=datetime(2026, 4, 30, 8, 14, 32, tzinfo=timezone.utc),
        body_md="Hello world.",
    )
    base.update(overrides)
    return NewsItem(**base)


def test_slugify_simple():
    s = slugify_subject("Weekly Roundup #42", message_id="abc@x.com")
    assert s.startswith("weekly-roundup-42-")
    assert len(s.rsplit("-", 1)[1]) == 6


def test_slugify_unicode_normalised():
    assert slugify_subject("Café", message_id="m").startswith("cafe-")


def test_slugify_empty_subject():
    assert slugify_subject("", message_id="m").startswith("untitled-")


def test_slugify_long_subject_truncates_to_60_plus_hash():
    base, suffix = slugify_subject("a" * 200, message_id="m").rsplit("-", 1)
    assert len(base) == 60
    assert len(suffix) == 6


def test_write_news_item_path_layout(tmp_path):
    path = write_news_item(_newsletter(), tmp_path)
    rel = path.relative_to(tmp_path)
    assert rel.parts[:3] == ("00-Inbox", "news", "2026-04-30")
    assert rel.suffix == ".md"


def test_write_news_item_frontmatter_keys(tmp_path):
    import yaml
    path = write_news_item(_newsletter(), tmp_path)
    content = path.read_text()
    assert content.startswith("---\n")
    fm_end = content.index("\n---\n", 4)
    fm = yaml.safe_load(content[4:fm_end])
    assert fm == {
        "type": "news-item",
        "created": "2026-04-30",
        "received-at": "2026-04-30T08:14:32+00:00",
        "source": "Example Newsletter",
        "source-type": "newsletter",
        "source-address": "newsletter@example.com",
        "subject": "Weekly Roundup #42",
        "message-id": "abc123@example.com",
        "tags": ["news", "source-newsletter"],
    }


def test_write_news_item_body_after_frontmatter(tmp_path):
    path = write_news_item(_newsletter(body_md="Body here.\n\n"), tmp_path)
    content = path.read_text()
    assert content.endswith("Body here.\n")
    assert "---\n\nBody here." in content


def test_extras_flow_into_frontmatter(tmp_path):
    item = NewsItem(
        message_id="hn-12345",
        source="Hacker News",
        source_type="hacker-news",
        source_address="https://news.ycombinator.com/item?id=12345",
        subject="Show HN: Something cool",
        received_at=datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc),
        body_md="...",
        extra={"hn-points": 142, "hn-comments": 37},
    )
    content = write_news_item(item, tmp_path).read_text()
    import yaml
    fm_end = content.index("\n---\n", 4)
    fm = yaml.safe_load(content[4:fm_end])
    assert fm["hn-points"] == 142
    assert fm["hn-comments"] == 37
    assert fm["tags"] == ["news", "source-hacker-news"]
    # Extras land between canonical fields and tags so newsletter (extra={})
    # stays byte-identical with the historical writer.
    keys = list(fm.keys())
    assert keys.index("hn-points") > keys.index("message-id")
    assert keys.index("hn-points") < keys.index("tags")


def test_extras_cannot_collide_with_canonical_keys():
    with pytest.raises(ValueError, match="collides"):
        NewsItem(
            message_id="x",
            source="x",
            source_type="newsletter",
            source_address="x",
            subject="x",
            received_at=datetime(2026, 4, 30, tzinfo=timezone.utc),
            body_md="",
            extra={"subject": "trying to override"},
        )


def test_received_at_must_be_tz_aware():
    with pytest.raises(ValueError, match="timezone-aware"):
        NewsItem(
            message_id="x",
            source="x",
            source_type="newsletter",
            source_address="x",
            subject="x",
            received_at=datetime(2026, 4, 30, 0, 0, 0),
            body_md="",
        )


def test_unknown_source_type_rejected():
    with pytest.raises(ValueError, match="unknown source_type"):
        NewsItem(
            message_id="x",
            source="x",
            source_type="reddit",  # type: ignore[arg-type]
            source_address="x",
            subject="x",
            received_at=datetime(2026, 4, 30, tzinfo=timezone.utc),
            body_md="",
        )


def test_idempotent_overwrite(tmp_path):
    item = _newsletter()
    p1 = write_news_item(item, tmp_path)
    p2 = write_news_item(item, tmp_path)
    assert p1 == p2
    assert p1.read_text() == p2.read_text()
