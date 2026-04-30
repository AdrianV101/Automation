"""Byte-identical frontmatter regression test.

Locks the news-item frontmatter schema. The Phase 1 master-doc generator and
forthcoming Phase 2 sources (HN/FT/X) read these keys; a silent rename or
reorder would break them invisibly.

If you intentionally change the schema, regenerate the golden files via the
snippet in libs/news-pipeline/README.md and update downstream consumers in the
same PR.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from email_ingest import parse_email
from news_pipeline import write_news_item
from audio_ingest.news_email_adapter import email_to_news_item, render_body

FIXTURES = Path(__file__).parent / "fixtures" / "news"
GOLDEN = FIXTURES / "golden"


@pytest.mark.parametrize("eml_name", ["html_newsletter.eml", "plaintext_newsletter.eml"])
def test_newsletter_frontmatter_byte_identical(eml_name):
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
