"""Test the dynamic extraction system-prompt builder."""
from pathlib import Path

from automation_daemon.extraction import build_extraction_system_prompt


def test_builder_includes_known_people_section_empty(tmp_path: Path):
    prompt = build_extraction_system_prompt(tmp_path)
    assert "## Known People\n(none)" in prompt
    assert "Routing Rules" in prompt  # rest of the prompt still present


def test_builder_includes_known_people_section_populated(tmp_path: Path):
    folder = tmp_path / "03-Resources" / "People"
    folder.mkdir(parents=True)
    (folder / "Adrian Verhoosel Azpiroz.md").write_text(
        "---\n"
        "type: person\n"
        "relationship: \"self / system owner\"\n"
        "description: \"the user, building this system\"\n"
        "tags: [person]\n"
        "---\n"
        "# Adrian Verhoosel Azpiroz\n",
        encoding="utf-8",
    )
    prompt = build_extraction_system_prompt(tmp_path)
    assert "- Adrian Verhoosel Azpiroz - the user, building this system" in prompt
