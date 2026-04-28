"""Vault-backed people roster loader and prompt renderers.

Reads person notes from {vault}/03-Resources/People/. Each note has YAML
frontmatter with type, relationship, description, and optional aliases/projects.
File basename is the canonical full name and matches any associated speaker
profile filename (single source of truth for the roster).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

PEOPLE_FOLDER = Path("03-Resources") / "People"


@dataclass(frozen=True)
class Person:
    name: str
    relationship: str
    description: str
    aliases: tuple[str, ...] = ()

    @property
    def is_stub(self) -> bool:
        return not self.relationship or not self.description


def load_people(vault_path: Path) -> list[Person]:
    """Read all person notes from {vault}/03-Resources/People/.

    Returns an empty list if the folder doesn't exist. Notes with malformed
    YAML are logged and skipped. Notes without `type: person` are ignored.
    Result is sorted alphabetically by name.
    """
    folder = vault_path / PEOPLE_FOLDER
    if not folder.is_dir():
        return []
    people: list[Person] = []
    for path in sorted(folder.glob("*.md")):
        person = _parse_person_note(path)
        if person is not None:
            people.append(person)
    return people


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_person_note(path: Path) -> Person | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        log.warning("Could not read person note %s", path, exc_info=True)
        return None
    match = _FRONTMATTER_RE.match(text)
    if not match:
        log.warning("Person note %s has no frontmatter; skipping", path)
        return None
    fm = _parse_simple_yaml(match.group(1))
    if fm.get("type") != "person":
        return None
    return Person(
        name=path.stem,
        relationship=str(fm.get("relationship", "") or ""),
        description=str(fm.get("description", "") or ""),
        aliases=tuple(fm.get("aliases", []) or ()),
    )


def _parse_simple_yaml(text: str) -> dict:
    """Parse YAML frontmatter using PyYAML (already in the daemon venv)."""
    import yaml
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        log.warning("Malformed YAML frontmatter; skipping note", exc_info=True)
        return {}
    return loaded if isinstance(loaded, dict) else {}


def render_full_block(people: list[Person]) -> str:
    """Verbose block for the extraction agent."""
    if not people:
        return "## Known People\n(none)"
    lines = ["## Known People"]
    for p in sorted(people, key=lambda p: p.name):
        if p.is_stub:
            lines.append(f"- {p.name}")
        else:
            lines.append(f"- {p.name} - {p.description}")
    return "\n".join(lines)


def render_oneliner(people: list[Person]) -> str:
    """Compact line for command agent prompts."""
    if not people:
        return "Known people: (none)"
    parts: list[str] = []
    for p in sorted(people, key=lambda p: p.name):
        if p.is_stub:
            parts.append(p.name)
        else:
            parts.append(f"{p.name} ({p.relationship})")
    return "Known people: " + ", ".join(parts)
