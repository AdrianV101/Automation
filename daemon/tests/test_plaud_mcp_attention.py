from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from automation_daemon.plaud_mcp_attention import (
    ATTENTION_NOTE_REL,
    render_attention,
    write_attention_note,
)
from automation_daemon.plaud_mcp_state import PlaudRow
from automation_daemon.plaud_mcp_trigger import TrackState

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def _row(file_id: str = "f_abc", name: str = "Team sync", error: str = "boom") -> PlaudRow:
    return PlaudRow(
        file_id=file_id,
        name=name,
        status="failed",
        track=TrackState(ready_at=NOW, speaker_map={}, last_label_change_at=None),
        attempts=3,
        error=error,
        transcript_path=None,
        first_seen_at=NOW,
        ingested_at=None,
    )


def test_healthy_renders_none_and_deletes(tmp_path: Path) -> None:
    note = tmp_path / ATTENTION_NOTE_REL
    note.parent.mkdir(parents=True)
    note.write_text("old")
    content = render_attention([], None)
    assert content is None
    write_attention_note(tmp_path, content)
    assert not note.exists()
    write_attention_note(tmp_path, None)


def test_failed_rows_rendered() -> None:
    out = render_attention([_row()], None)
    assert out is not None
    assert "# Plaud ingest needs attention" in out
    assert "tags: [automation, plaud, attention]" in out
    assert "type: fleeting" in out
    assert "## Failed recordings" in out
    assert "- **Team sync** (`f_abc`): boom." in out
    assert "Re-run: `daemon/.venv/bin/python -m automation_daemon plaud-ingest f_abc`" in out
    assert "## Plaud MCP" not in out


def test_mcp_problem_rendered() -> None:
    out = render_attention([], "login expired")
    assert out is not None
    assert "## Plaud MCP" in out
    assert "login expired" in out
    assert "## Failed recordings" not in out


def test_unchanged_note_not_rewritten(tmp_path: Path) -> None:
    content = render_attention([_row()], None)
    write_attention_note(tmp_path, content)
    note = tmp_path / ATTENTION_NOTE_REL
    os.utime(note, ns=(1_000_000_000, 1_000_000_000))
    before = note.stat().st_mtime_ns
    write_attention_note(tmp_path, content)
    assert note.stat().st_mtime_ns == before


def test_changed_content_rewritten(tmp_path: Path) -> None:
    write_attention_note(tmp_path, render_attention([_row(error="one")], None))
    new = render_attention([_row(error="two")], None)
    write_attention_note(tmp_path, new)
    assert (tmp_path / ATTENTION_NOTE_REL).read_bytes() == new.encode("utf-8")


def test_created_preserved_and_unchanged_set_not_rewritten(
    tmp_path: Path, monkeypatch
) -> None:
    from automation_daemon import plaud_mcp_attention as mod

    class _Day1(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 7, tzinfo=timezone.utc)

    class _Day2(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 8, tzinfo=timezone.utc)

    monkeypatch.setattr(mod, "datetime", _Day1)
    write_attention_note(tmp_path, render_attention([_row()], None))
    note = tmp_path / ATTENTION_NOTE_REL
    os.utime(note, ns=(1_000_000_000, 1_000_000_000))
    before = note.stat().st_mtime_ns
    monkeypatch.setattr(mod, "datetime", _Day2)
    write_attention_note(tmp_path, render_attention([_row()], None))
    assert note.stat().st_mtime_ns == before
    assert "created: 2026-11-07" in note.read_text()


def test_newlines_in_name_and_error_collapsed() -> None:
    out = render_attention([_row(name="a\nb", error="x\n## injected")], None)
    assert out is not None
    assert "- **a b** (`f_abc`): x ## injected." in out
    assert "\n## injected" not in out


def test_rerun_line_warns_when_notes_may_exist() -> None:
    caution = "(notes may already exist; re-running may duplicate them)"
    rerun = "Re-run: `daemon/.venv/bin/python -m automation_daemon plaud-ingest"
    for error, warned in [
        ("extraction: agent broke", True),
        ("interrupted", True),
        ("pipeline broke", False),
    ]:
        out = render_attention([_row(error=error)], None)
        assert out is not None
        line = next(ln for ln in out.splitlines() if rerun in ln)
        assert line.endswith(f"plaud-ingest f_abc` {caution}") is warned
        assert (caution in out) is warned
