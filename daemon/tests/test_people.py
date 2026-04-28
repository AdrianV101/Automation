"""Tests for the people-folder loader and prompt renderers."""
from pathlib import Path

import pytest

from audio_ingest.people import (
    Person,
    load_people,
    render_full_block,
    render_oneliner,
)


def _write_person(folder: Path, name: str, body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(body, encoding="utf-8")


def _full_record(name: str, relationship: str, description: str) -> str:
    return (
        f"---\n"
        f"type: person\n"
        f"created: 2026-04-28\n"
        f"relationship: \"{relationship}\"\n"
        f"description: \"{description}\"\n"
        f"aliases: []\n"
        f"projects: []\n"
        f"tags: [person]\n"
        f"---\n"
        f"# {name}\n"
    )


def _stub_record(name: str) -> str:
    return (
        f"---\n"
        f"type: person\n"
        f"created: 2026-04-28\n"
        f"relationship: \"\"\n"
        f"description: \"\"\n"
        f"aliases: []\n"
        f"projects: []\n"
        f"tags: [person, stub]\n"
        f"---\n"
        f"# {name}\n"
    )


class TestLoadPeople:
    def test_returns_empty_list_when_folder_missing(self, tmp_path: Path):
        # vault root exists but 03-Resources/People/ does not
        assert load_people(tmp_path) == []

    def test_returns_empty_list_when_folder_empty(self, tmp_path: Path):
        (tmp_path / "03-Resources" / "People").mkdir(parents=True)
        assert load_people(tmp_path) == []

    def test_loads_full_record(self, tmp_path: Path):
        folder = tmp_path / "03-Resources" / "People"
        _write_person(folder, "Sarah Verhoosel", _full_record("Sarah Verhoosel", "partner", "close friend"))
        people = load_people(tmp_path)
        assert len(people) == 1
        assert people[0] == Person(name="Sarah Verhoosel", relationship="partner", description="close friend", aliases=())
        assert people[0].is_stub is False

    def test_detects_stub(self, tmp_path: Path):
        folder = tmp_path / "03-Resources" / "People"
        _write_person(folder, "Jane Doe", _stub_record("Jane Doe"))
        people = load_people(tmp_path)
        assert len(people) == 1
        assert people[0].is_stub is True

    def test_skips_non_person_notes(self, tmp_path: Path):
        folder = tmp_path / "03-Resources" / "People"
        folder.mkdir(parents=True)
        (folder / "Not Person.md").write_text(
            "---\ntype: research\n---\n# Not Person\n", encoding="utf-8",
        )
        assert load_people(tmp_path) == []

    def test_skips_malformed_yaml(self, tmp_path: Path, caplog):
        folder = tmp_path / "03-Resources" / "People"
        folder.mkdir(parents=True)
        (folder / "Broken.md").write_text(
            "---\ntype: person\nrelationship: [unclosed\n---\n# Broken\n", encoding="utf-8",
        )
        assert load_people(tmp_path) == []

    def test_sorts_alphabetically(self, tmp_path: Path):
        folder = tmp_path / "03-Resources" / "People"
        _write_person(folder, "Beta", _full_record("Beta", "x", "x"))
        _write_person(folder, "Alpha", _full_record("Alpha", "x", "x"))
        names = [p.name for p in load_people(tmp_path)]
        assert names == ["Alpha", "Beta"]

    def test_loads_aliases_as_tuple(self, tmp_path: Path):
        folder = tmp_path / "03-Resources" / "People"
        body = (
            "---\ntype: person\nrelationship: x\ndescription: x\n"
            "aliases: [AV, av]\ntags: [person]\n---\n# X\n"
        )
        _write_person(folder, "X", body)
        people = load_people(tmp_path)
        assert people[0].aliases == ("AV", "av")


class TestRenderFullBlock:
    def test_empty_renders_none(self):
        assert render_full_block([]) == "## Known People\n(none)"

    def test_full_record_renders_with_description(self):
        people = [Person(name="Sarah", relationship="partner", description="close friend")]
        assert render_full_block(people) == "## Known People\n- Sarah - close friend"

    def test_stub_renders_bare_name(self):
        people = [Person(name="Jane", relationship="", description="")]
        assert render_full_block(people) == "## Known People\n- Jane"

    def test_mixed_sorted(self):
        people = [
            Person(name="Beta", relationship="", description=""),
            Person(name="Alpha", relationship="r", description="d"),
        ]
        out = render_full_block(people)
        assert out == "## Known People\n- Alpha - d\n- Beta"


class TestRenderOneliner:
    def test_empty_renders_none(self):
        assert render_oneliner([]) == "Known people: (none)"

    def test_full_record_renders_with_relationship(self):
        people = [Person(name="Leo", relationship="co-founder", description="x")]
        assert render_oneliner(people) == "Known people: Leo (co-founder)"

    def test_stub_renders_bare_name(self):
        people = [Person(name="Jane", relationship="", description="")]
        assert render_oneliner(people) == "Known people: Jane"

    def test_mixed_sorted(self):
        people = [
            Person(name="Beta", relationship="", description=""),
            Person(name="Alpha", relationship="x", description="y"),
        ]
        assert render_oneliner(people) == "Known people: Alpha (x), Beta"
