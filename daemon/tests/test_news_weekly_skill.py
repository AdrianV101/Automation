from __future__ import annotations

from pathlib import Path

_SKILL = (
    Path(__file__).resolve().parents[2]
    / ".claude" / "skills" / "news-weekly-patterns" / "SKILL.md"
)


def test_skill_file_exists() -> None:
    assert _SKILL.is_file()


def test_skill_frontmatter_has_name_and_description() -> None:
    text = _SKILL.read_text()
    assert text.startswith("---")
    assert "name: news-weekly-patterns" in text
    assert "description:" in text


def test_skill_documents_key_invariants() -> None:
    text = _SKILL.read_text()
    assert "threads_written" in text
    assert "entities_enriched" in text
    assert "```json" in text
    assert "## Notes" in text
    assert "Recurring threads (deterministic" in text
    assert "one" in text.lower()
    assert "01-Projects/News/weekly/" in text
    assert "01-Projects/News/entities/" in text
