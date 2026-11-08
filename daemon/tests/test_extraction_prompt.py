"""Test the dynamic extraction system-prompt builder."""
from pathlib import Path

from automation_daemon.extraction import _build_user_prompt, build_extraction_system_prompt
from pkm import TranscriptData, TranscriptSegment


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


def _transcript() -> TranscriptData:
    return TranscriptData(
        job_id="x",
        recorded_at="2024-01-01T00:00:00Z",
        duration_seconds=120.0,
        speakers=["Speaker_1"],
        segments=[TranscriptSegment(start=0, end=5, speaker="Speaker_1", text="note")],
        full_text="note",
    )


def test_prompt_includes_outline() -> None:
    prompt = _build_user_prompt(
        _transcript(), Path("/vault/t.md"),
        source_metadata={"plaud_outline": "1. Intro"},
    )
    assert "### Plaud outline\n1. Intro" in prompt


def test_prompt_includes_highlights() -> None:
    prompt = _build_user_prompt(
        _transcript(), Path("/vault/t.md"),
        source_metadata={"plaud_highlights": [{"time_s": 65.0, "text": "We ship Friday."}]},
    )
    assert (
        "Adrian flagged these moments during the recording with the device's "
        "highlight button. Each one must be reflected in the notes you route:\n"
        "- [01:05] We ship Friday."
    ) in prompt
