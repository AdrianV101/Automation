"""Byte-identical frontmatter regression test.

Locks the news-item frontmatter schema. The master-doc generator and other
news sources read these keys; a silent rename or reorder would break them
invisibly. To intentionally change the schema, regenerate the goldens per
libs/news-pipeline/README.md and audit every downstream consumer in the
same PR.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from email_ingest import parse_email
from news_pipeline import write_news_item
from automation_daemon.news_email_adapter import email_to_news_item, render_body

FIXTURES = Path(__file__).parent / "fixtures" / "news"
GOLDEN = FIXTURES / "golden"


@pytest.mark.parametrize(
    "eml_name",
    ["html_newsletter.eml", "plaintext_newsletter.eml", "firstft_email.eml"],
)
def test_news_item_frontmatter_byte_identical(eml_name):
    parsed = parse_email((FIXTURES / eml_name).read_bytes())
    body_md = render_body(parsed)
    item = email_to_news_item(parsed, body_md)
    with tempfile.TemporaryDirectory() as td:
        path = write_news_item(item, Path(td))
        content = path.read_text(encoding="utf-8")
    fm_end = content.index("\n---\n", 4)
    actual_frontmatter = content[: fm_end + len("\n---\n")]

    golden_path = GOLDEN / eml_name.replace(".eml", ".frontmatter")
    expected = golden_path.read_text(encoding="utf-8")
    assert actual_frontmatter == expected, (
        f"frontmatter drift for {eml_name}; "
        f"if intentional, regenerate {golden_path.relative_to(FIXTURES.parent.parent)} "
        f"per libs/news-pipeline/README.md"
    )
