"""Tests for the dynamic system-prompt builders."""
from pathlib import Path

import pytest

from audio_ingest.prompts import (
    build_ask_system_prompt,
    build_chat_system_prompt,
    build_note_system_prompt,
    build_task_system_prompt,
)


def _seed_person(vault: Path, name: str, relationship: str, description: str) -> None:
    folder = vault / "03-Resources" / "People"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(
        "---\n"
        "type: person\n"
        f"relationship: \"{relationship}\"\n"
        f"description: \"{description}\"\n"
        "tags: [person]\n"
        "---\n"
        f"# {name}\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("builder", [
    build_note_system_prompt,
    build_task_system_prompt,
    build_ask_system_prompt,
    build_chat_system_prompt,
])
def test_empty_roster_renders_none(builder, tmp_path):
    prompt = builder(tmp_path)
    assert "Known people: (none)" in prompt


@pytest.mark.parametrize("builder", [
    build_note_system_prompt,
    build_task_system_prompt,
    build_ask_system_prompt,
    build_chat_system_prompt,
])
def test_populated_roster_renders_oneliner(builder, tmp_path):
    _seed_person(tmp_path, "Adrian Verhoosel Azpiroz", "self / system owner", "the user")
    prompt = builder(tmp_path)
    assert "Adrian Verhoosel Azpiroz (self / system owner)" in prompt
    assert prompt.count("Adrian Verhoosel Azpiroz (self / system owner)") == 1


def test_note_prompt_keeps_critical_constraints(tmp_path):
    prompt = build_note_system_prompt(tmp_path)
    assert "MUST NOT" in prompt
    assert "vault_semantic_search" in prompt
