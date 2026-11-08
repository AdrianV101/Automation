"""Shared fakes for the Plaud MCP ingest tests."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from automation_daemon.config import DaemonConfig

T0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
SWITCHOVER = date(2026, 11, 7)

GENERIC = {"Speaker 1": "Speaker 1", "Speaker 2": "Speaker 2"}
PARTIAL = {"Speaker 1": "Joel Low (Scope)", "Speaker 2": "Speaker 2"}
NAMED = {"Speaker 1": "Joel Low (Scope)", "Speaker 2": "Adrian Verhoosel"}


def make_config(tmp_path: Path) -> DaemonConfig:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    return DaemonConfig(
        pkm_vault_path=vault,
        email_ingest_state_db_path=tmp_path / "state.db",
        plaud_mcp_switchover_date=SWITCHOVER,
    )


def make_segments(mapping: dict[str, str]) -> list[dict]:
    segments = []
    for i, (original, speaker) in enumerate(mapping.items()):
        segments.append({
            "start_time": i * 2000,
            "end_time": i * 2000 + 1500,
            "content": f"segment {i}",
            "speaker": speaker,
            "original_speaker": original,
        })
    return segments


def make_detail(file_id: str, name: str = "Meeting", *, ready: bool = True) -> dict:
    source_list = [{"data_type": "mark_memo", "data_content": ""}]
    note_list: list[dict] = []
    if ready:
        source_list.append({"data_type": "transaction", "data_content": ""})
        note_list.append({
            "data_type": "auto_sum_note",
            "data_title": "Summary",
            "data_tab_name": "Summary",
            "data_content": "summary text",
        })
    return {
        "id": file_id,
        "name": name,
        "start_at": "2026-11-07T10:00:00",
        "duration": 60000,
        "presigned_url": "",
        "source_list": source_list,
        "note_list": note_list,
    }


class FakePlaudClient:
    """In-memory stand-in for PlaudMcpClient."""

    def __init__(self) -> None:
        self.details: dict[str, dict] = {}
        self.segments: dict[str, list[dict]] = {}
        self.marks: dict[str, list[dict]] = {}
        self.errors: dict[tuple[str, str], Exception] = {}
        self.list_calls: list[tuple[date, date]] = []
        self.get_file_calls: list[str] = []

    def add(
        self, file_id: str, mapping: dict[str, str], *,
        name: str = "Meeting", ready: bool = True, segments: list[dict] | None = None,
    ) -> None:
        self.details[file_id] = make_detail(file_id, name, ready=ready)
        self.segments[file_id] = make_segments(mapping) if segments is None else segments
        self.marks[file_id] = []

    def set_map(self, file_id: str, mapping: dict[str, str]) -> None:
        self.segments[file_id] = make_segments(mapping)

    async def __aenter__(self) -> FakePlaudClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def _maybe_raise(self, method: str, file_id: str) -> None:
        exc = self.errors.get((method, file_id))
        if exc is not None:
            raise exc

    async def list_files(self, date_from: date, date_to: date) -> list[dict]:
        self.list_calls.append((date_from, date_to))
        return [
            {
                "id": d["id"], "name": d["name"], "created_at": d["start_at"],
                "serial_number": "SN", "start_at": d["start_at"], "duration": d["duration"],
            }
            for d in self.details.values()
        ]

    async def get_file(self, file_id: str) -> dict:
        self.get_file_calls.append(file_id)
        self._maybe_raise("get_file", file_id)
        return self.details[file_id]

    async def get_transcript(self, file_id: str) -> list[dict]:
        self._maybe_raise("get_transcript", file_id)
        return self.segments[file_id]

    async def get_marks(self, file_id: str) -> list[dict]:
        self._maybe_raise("get_marks", file_id)
        return self.marks[file_id]


class FakeProcess:
    """Fake process_recording that reports a scripted outcome to the tracker."""

    def __init__(self, outcome: str = "completed", transcript_path: str = "/vault/t.md") -> None:
        self.outcome = outcome
        self.transcript_path = transcript_path
        self.calls: list[dict[str, Any]] = []
        self.hook = None

    async def __call__(
        self, job, config, status=None, speakers_unresolved: bool = False, *, notify: bool = True,
    ) -> None:
        self.calls.append({
            "job": job, "speakers_unresolved": speakers_unresolved, "notify": notify,
        })
        if self.hook is not None:
            await self.hook(job)
        await status.update("writing_pkm")
        if self.outcome == "completed":
            await status.update("completed", transcript_path=self.transcript_path, summary_path="")
        elif self.outcome == "extraction_error":
            await status.update(
                "completed", transcript_path=self.transcript_path, error="agent broke",
            )
        elif self.outcome == "failed":
            await status.update("failed", error="pipeline broke")


def minutes(n: float) -> timedelta:
    return timedelta(minutes=n)
