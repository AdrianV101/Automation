"""Vault attention note listing failed Plaud MCP ingests and MCP problems."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .plaud_mcp_state import PlaudRow, notes_may_exist

_CREATED = re.compile(r"^created: .*$", re.MULTILINE)
_DUPLICATE_CAUTION = "(notes may already exist; re-running may duplicate them)"
ATTENTION_NOTE_REL = Path("00-Inbox/plaud-ingest-attention.md")


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def render_attention(failed: list[PlaudRow], mcp_problem: str | None) -> str | None:
    if not failed and mcp_problem is None:
        return None
    created = datetime.now(timezone.utc).date().isoformat()
    lines = [
        "---",
        "type: fleeting",
        f"created: {created}",
        "tags: [automation, plaud, attention]",
        "---",
        "",
        "# Plaud ingest needs attention",
        "",
    ]
    if mcp_problem is not None:
        lines += ["## Plaud MCP", "", mcp_problem, ""]
    if failed:
        lines += ["## Failed recordings", ""]
        for row in failed:
            caution = f" {_DUPLICATE_CAUTION}" if notes_may_exist(row) else ""
            lines.append(
                f"- **{_one_line(row.name)}** (`{row.file_id}`): {_one_line(row.error)}. "
                f"Re-run: `daemon/.venv/bin/python -m automation_daemon "
                f"plaud-ingest {row.file_id}`{caution}"
            )
        lines.append("")
    return "\n".join(lines)


def check_attention_writable(vault_path: Path) -> None:
    """Raise OSError unless the note's folder in an existing vault is writable."""
    if not vault_path.is_dir():
        raise FileNotFoundError(f"vault path {vault_path} is not a directory")
    folder = (vault_path / ATTENTION_NOTE_REL).parent
    folder.mkdir(exist_ok=True)
    probe = folder / ".plaud-ingest-attention.probe"
    probe.write_bytes(b"")
    probe.unlink()


def write_attention_note(vault_path: Path, content: str | None) -> None:
    note = vault_path / ATTENTION_NOTE_REL
    if content is None:
        note.unlink(missing_ok=True)
        return
    if note.exists():
        existing = note.read_text(encoding="utf-8")
        found = _CREATED.search(existing)
        if found:
            content = _CREATED.sub(lambda _m: found.group(0), content, count=1)
    data = content.encode("utf-8")
    if note.exists() and note.read_bytes() == data:
        return
    note.parent.mkdir(parents=True, exist_ok=True)
    tmp = note.with_name(note.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, note)
