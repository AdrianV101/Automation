from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from plaud_mcp_fakes import NAMED, T0, FakePlaudClient, FakeProcess, make_config

from automation_daemon.plaud_mcp_attention import ATTENTION_NOTE_REL
from automation_daemon.plaud_mcp_client import PlaudAuthError, PlaudMcpError
from automation_daemon.plaud_mcp_ingest import run_manual_ingest, run_plaud_mcp_forever
from automation_daemon.plaud_mcp_state import PlaudMcpStateDB
from automation_daemon.plaud_mcp_trigger import TrackState


@pytest.fixture
def config(tmp_path):
    return make_config(tmp_path)


async def _seed_completed(config, file_id: str) -> PlaudMcpStateDB:
    state = PlaudMcpStateDB(config.email_ingest_state_db_path)
    await state.init_db()
    track = TrackState(ready_at=T0, speaker_map=NAMED, last_label_change_at=None)
    await state.upsert_waiting(file_id, "Meeting", track, T0)
    await state.mark_ingesting(file_id)
    await state.mark_completed(file_id, "/vault/old.md", T0)
    return state


async def _run(config, client, process, *, force: bool = False, extract: bool = False):
    lines: list[str] = []
    code = await run_manual_ingest(
        config, "f_a", force=force, extract=extract,
        client_factory=lambda: client, process_recording_fn=process, out=lines.append,
    )
    return code, lines


async def test_manual_not_ready_returns_1(config):
    client = FakePlaudClient()
    client.add("f_a", NAMED, ready=False)
    process = FakeProcess()
    code, lines = await _run(config, client, process)
    assert code == 1
    assert lines == ["Recording f_a is not ready (no transcript or summary yet)."]
    assert process.calls == []


async def test_manual_completed_without_force_returns_1(config):
    await _seed_completed(config, "f_a")
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    process = FakeProcess()
    code, lines = await _run(config, client, process)
    assert code == 1
    assert lines == [
        "Already ingested; use --force to rewrite the transcript "
        "(add --extract to re-run extraction)."
    ]
    assert process.calls == []


async def test_manual_force_without_extract_rewrites_transcript(config):
    state = await _seed_completed(config, "f_a")
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    process = FakeProcess()
    code, lines = await _run(config, client, process, force=True)
    assert code == 0
    assert process.calls == []
    written = Path(lines[-1])
    assert written.is_file()
    assert config.pkm_vault_path in written.parents
    row = await state.get("f_a")
    assert row.status == "completed"
    assert row.transcript_path == str(written)


async def test_manual_force_and_extract_warns_and_processes(config):
    await _seed_completed(config, "f_a")
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    process = FakeProcess()
    code, lines = await _run(config, client, process, force=True, extract=True)
    assert (
        "Warning: re-running extraction may duplicate notes already routed "
        "for this recording."
    ) in lines
    assert len(process.calls) == 1
    assert process.calls[0]["notify"] is False
    assert code == 0


async def test_manual_new_id_ingested(config):
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    process = FakeProcess(transcript_path="/vault/new.md")
    code, _lines = await _run(config, client, process)
    assert code == 0
    assert len(process.calls) == 1
    state = PlaudMcpStateDB(config.email_ingest_state_db_path)
    row = await state.get("f_a")
    assert row.status == "completed"
    assert row.transcript_path == "/vault/new.md"


_WARNING = (
    "Warning: re-running extraction may duplicate notes already routed "
    "for this recording."
)


@pytest.mark.parametrize(
    ("seed", "force", "extract"),
    [(None, False, False), ("completed", True, True), ("failed", False, False)],
    ids=["new", "force-extract", "retry-failed"],
)
async def test_manual_failure_lands_in_failed(config, seed, force, extract):
    if seed == "completed":
        await _seed_completed(config, "f_a")
    elif seed == "failed":
        state = await _seed_completed(config, "f_a")
        await state.mark_failed("f_a", "interrupted")
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    process = FakeProcess(outcome="failed")
    code, lines = await _run(config, client, process, force=force, extract=extract)
    assert code == 1
    assert len(process.calls) == 1
    row = await PlaudMcpStateDB(config.email_ingest_state_db_path).get("f_a")
    assert (row.status, row.error) == ("failed", "pipeline broke")
    assert lines[-1] == "Ingest of f_a did not complete (status failed): pipeline broke"


async def _seed_ingesting(config, file_id: str) -> PlaudMcpStateDB:
    state = PlaudMcpStateDB(config.email_ingest_state_db_path)
    await state.init_db()
    track = TrackState(ready_at=T0, speaker_map=NAMED, last_label_change_at=None)
    await state.upsert_waiting(file_id, "Meeting", track, T0)
    await state.mark_ingesting(file_id)
    return state


@pytest.mark.parametrize("extract", [False, True])
async def test_manual_refuses_ingesting_row_without_force(config, extract):
    state = await _seed_ingesting(config, "f_a")
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    process = FakeProcess()
    code, lines = await _run(config, client, process, extract=extract)
    assert code == 1
    assert lines == [
        "Recording f_a is marked as being ingested right now. If no ingest is "
        "running (e.g. a previous run was killed), re-run with --force."
    ]
    assert process.calls == []
    assert (await state.get("f_a")).status == "ingesting"


async def test_manual_force_overrides_ingesting_row(config):
    state = await _seed_ingesting(config, "f_a")
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    process = FakeProcess(transcript_path="/vault/forced.md")
    code, lines = await _run(config, client, process, force=True)
    assert code == 0
    assert _WARNING in lines
    assert len(process.calls) == 1
    row = await state.get("f_a")
    assert (row.status, row.transcript_path) == ("completed", "/vault/forced.md")


@pytest.mark.parametrize(
    ("error", "warned"),
    [("extraction: agent broke", True), ("interrupted", True), ("pipeline broke", False)],
)
async def test_manual_failed_row_duplicate_warning(config, error, warned):
    state = await _seed_completed(config, "f_a")
    await state.mark_failed("f_a", error)
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    process = FakeProcess()
    code, lines = await _run(config, client, process)
    assert code == 0
    assert (_WARNING in lines) is warned
    assert len(process.calls) == 1


async def test_manual_marks_fetch_error_lands_in_failed(config):
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    client.errors[("get_marks", "f_a")] = PlaudMcpError("get_transcript timed out")
    process = FakeProcess()
    code, lines = await _run(config, client, process)
    assert code == 1
    assert any("get_transcript timed out" in line for line in lines)
    assert process.calls == []
    row = await PlaudMcpStateDB(config.email_ingest_state_db_path).get("f_a")
    assert (row.status, row.attempts) == ("failed", 1)


async def test_manual_auth_error_prints_login_hint(config):
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    client.errors[("get_marks", "f_a")] = PlaudAuthError("Not authenticated")
    code, lines = await _run(config, client, FakeProcess())
    assert code == 1
    assert any("login" in line.lower() and "Not authenticated" in line for line in lines)
    row = await PlaudMcpStateDB(config.email_ingest_state_db_path).get("f_a")
    assert (row.status, row.attempts) == ("waiting_labels", 0)


async def test_manual_fix_of_failed_row_clears_attention_note(config):
    client = FakePlaudClient()
    client.add("f_a", NAMED)

    async def stop(_seconds: float) -> None:
        raise asyncio.CancelledError

    async def one_daemon_iteration(process: FakeProcess) -> None:
        with pytest.raises(asyncio.CancelledError):
            await run_plaud_mcp_forever(
                config, client_factory=lambda: client, process_recording_fn=process,
                sleep=stop, clock=lambda: T0,
            )

    await one_daemon_iteration(FakeProcess(outcome="extraction_error"))
    note = config.pkm_vault_path / ATTENTION_NOTE_REL
    assert "`f_a`" in note.read_text()

    code, lines = await _run(config, client, FakeProcess(transcript_path="/vault/fixed.md"))
    assert code == 0
    assert _WARNING in lines
    row = await PlaudMcpStateDB(config.email_ingest_state_db_path).get("f_a")
    assert (row.status, row.error, row.transcript_path) == (
        "completed", None, "/vault/fixed.md",
    )

    later = FakeProcess()
    await one_daemon_iteration(later)
    assert not note.exists()
    assert later.calls == []


async def test_manual_force_extract_on_new_id_no_warning(config):
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    process = FakeProcess()
    code, lines = await _run(config, client, process, force=True, extract=True)
    assert code == 0
    assert _WARNING not in lines
    assert len(process.calls) == 1
