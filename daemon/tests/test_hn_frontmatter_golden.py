"""Byte-identical frontmatter regression test for HN items.

Locks the frontmatter shape of HN-sourced news items. The master-doc
generator and other downstream consumers read these keys; a silent rename
or reorder would break them invisibly. To intentionally change the schema,
regenerate the golden via the recipe in libs/news-pipeline/README.md and
audit every downstream consumer in the same PR.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from news_pipeline import write_news_item
from automation_daemon.hacker_news_adapter import _to_news_item

FIXTURES = Path(__file__).parent / "fixtures" / "hn"
GOLDEN = FIXTURES / "golden"


def test_story_url_frontmatter_byte_identical():
    raw = json.loads((FIXTURES / "story_url.json").read_text())
    item = _to_news_item(raw)
    with tempfile.TemporaryDirectory() as td:
        path = write_news_item(item, Path(td))
        content = path.read_text(encoding="utf-8")
    fm_end = content.index("\n---\n", 4)
    actual = content[: fm_end + len("\n---\n")]

    golden_path = GOLDEN / "story_url.frontmatter"
    expected = golden_path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"frontmatter drift; if intentional, regenerate {golden_path} "
        f"per libs/news-pipeline/README.md"
    )
