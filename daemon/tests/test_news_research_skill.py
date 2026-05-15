from __future__ import annotations

from pathlib import Path

import pytest

_SKILL = (
    Path(__file__).resolve().parents[2]
    / ".claude" / "skills" / "news-research" / "SKILL.md"
)


@pytest.fixture
def skill_text() -> str:
    assert _SKILL.is_file(), f"missing skill file at {_SKILL}"
    return _SKILL.read_text()


def test_frontmatter_name(skill_text: str) -> None:
    assert skill_text.startswith("---")
    assert "name: news-research" in skill_text


def test_documents_json_summary_keys(skill_text: str) -> None:
    for key in ('"success"', '"items_researched"', '"error"'):
        assert key in skill_text


def test_states_notes_preservation_contract(skill_text: str) -> None:
    assert "## Notes" in skill_text
    assert "Deep dive" in skill_text


def test_states_bounded_depth(skill_text: str) -> None:
    # The bounded-depth rule (D6) must be explicit so the agent does not
    # follow links unboundedly.
    assert "3" in skill_text
    assert "git clone" in skill_text  # explicitly forbidden
