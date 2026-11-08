from __future__ import annotations

import asyncio
import dataclasses
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from agent_infra import AgentInfraConfigError
from plaud_mcp_fakes import (
    GENERIC,
    NAMED,
    PARTIAL,
    SWITCHOVER,
    T0,
    FakePlaudClient,
    FakeProcess,
    make_config,
    minutes,
)

from automation_daemon.extraction import AgentRoutingResult
from automation_daemon.plaud_mcp_attention import ATTENTION_NOTE_REL
from automation_daemon.plaud_mcp_client import PlaudAuthError, PlaudMcpError
from automation_daemon.plaud_mcp_ingest import (
    CycleReport,
    PlaudMcpSettings,
    run_plaud_mcp_forever,
    run_poll_cycle,
)
from automation_daemon.plaud_mcp_state import PlaudMcpStateDB
from automation_daemon.plaud_mcp_trigger import TrackState

SETTINGS = PlaudMcpSettings(switchover=SWITCHOVER)


@pytest.fixture
def config(tmp_path):
    return make_config(tmp_path)


@pytest.fixture
async def state(config):
    db = PlaudMcpStateDB(config.email_ingest_state_db_path)
    await db.init_db()
    return db


async def _cycle(client, state, config, process, now):
    return await run_poll_cycle(
        client, state, config, SETTINGS, now=now, process_recording_fn=process,
    )


async def test_not_ready_skipped(state, config):
    client = FakePlaudClient()
    client.add("f_a", NAMED, ready=False)
    process = FakeProcess()
    await _cycle(client, state, config, process, T0)
    assert await state.get("f_a") is None
    assert process.calls == []


async def test_empty_transcript_is_not_ready(state, config):
    client = FakePlaudClient()
    client.add("f_a", NAMED, segments=[])
    process = FakeProcess()
    report = await _cycle(client, state, config, process, T0)
    assert await state.get("f_a") is None
    assert process.calls == []
    assert report.ingested == []


async def test_all_named_ingests_with_notify_false(state, config):
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    process = FakeProcess(transcript_path="/vault/04-Archive/t.md")
    report = await _cycle(client, state, config, process, T0)
    assert len(process.calls) == 1
    assert process.calls[0]["notify"] is False
    assert process.calls[0]["speakers_unresolved"] is False
    row = await state.get("f_a")
    assert row.status == "completed"
    assert row.transcript_path == "/vault/04-Archive/t.md"
    assert report.ingested == ["f_a"]


async def test_untouched_generic_ingests_after_fallback(state, config):
    client = FakePlaudClient()
    client.add("f_a", GENERIC)
    process = FakeProcess()
    report = await _cycle(client, state, config, process, T0)
    assert report.waiting == 1
    assert (await state.get("f_a")).status == "waiting_labels"
    assert process.calls == []

    report = await _cycle(client, state, config, process, T0 + timedelta(hours=24))
    assert report.ingested == ["f_a"]
    assert process.calls[0]["speakers_unresolved"] is True


async def test_labelling_burst_waits_for_quiet(state, config):
    client = FakePlaudClient()
    client.add("f_a", GENERIC)
    process = FakeProcess()
    await _cycle(client, state, config, process, T0)
    client.set_map("f_a", PARTIAL)
    report = await _cycle(client, state, config, process, T0 + minutes(5))
    assert report.waiting == 1
    assert process.calls == []
    report = await _cycle(client, state, config, process, T0 + minutes(15))
    assert report.ingested == ["f_a"]
    assert len(process.calls) == 1


async def test_restart_preserves_quiet_timer(state, config):
    client = FakePlaudClient()
    client.add("f_a", GENERIC)
    process = FakeProcess()
    await _cycle(client, state, config, process, T0)
    client.set_map("f_a", PARTIAL)
    await _cycle(client, state, config, process, T0 + minutes(5))

    fresh = PlaudMcpStateDB(config.email_ingest_state_db_path)
    await fresh.init_db()
    report = await _cycle(client, fresh, config, process, T0 + minutes(10))
    assert report.waiting == 1
    assert process.calls == []
    row = await fresh.get("f_a")
    assert row.track.last_label_change_at == T0 + minutes(5)


async def test_rename_updates_row_no_duplicate(state, config):
    client = FakePlaudClient()
    client.add("f_a", GENERIC, name="Old title")
    process = FakeProcess()
    await _cycle(client, state, config, process, T0)
    client.details["f_a"]["name"] = "New title"
    await _cycle(client, state, config, process, T0 + minutes(5))
    row = await state.get("f_a")
    assert row.name == "New title"
    with sqlite3.connect(config.email_ingest_state_db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM plaud_mcp_recordings WHERE file_id = ?", ("f_a",),
        ).fetchone()[0]
    assert count == 1


@pytest.mark.parametrize(
    ("now", "switchover", "expected"),
    [
        (T0 + timedelta(days=3), SWITCHOVER, [(SWITCHOVER, date(2026, 11, 10))]),
        (T0, T0.date() - timedelta(days=30), [(T0.date() - timedelta(days=14), T0.date())]),
        (T0, T0.date() + timedelta(days=1), []),
    ],
    ids=["switchover-inside-window", "window-limited", "switchover-in-future"],
)
async def test_window_bounds(state, config, now, switchover, expected):
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    process = FakeProcess()
    settings = PlaudMcpSettings(switchover=switchover)
    report = await run_poll_cycle(
        client, state, config, settings, now=now, process_recording_fn=process,
    )
    assert client.list_calls == expected
    if not expected:
        assert report == CycleReport(ingested=[], waiting=0, failed=[])
        assert process.calls == []


@pytest.mark.parametrize(
    ("outcome", "final_status"),
    [("completed", "completed"), ("extraction_error", "failed")],
)
async def test_terminal_not_refetched(state, config, outcome, final_status):
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    process = FakeProcess(outcome=outcome)
    await _cycle(client, state, config, process, T0)
    assert (await state.get("f_a")).status == final_status
    client.get_file_calls.clear()
    await _cycle(client, state, config, process, T0 + minutes(5))
    assert client.get_file_calls == []
    assert len(process.calls) == 1


async def test_ingesting_committed_before_process(state, config):
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    process = FakeProcess()
    seen: list[str] = []

    async def hook(job) -> None:
        reader = PlaudMcpStateDB(config.email_ingest_state_db_path)
        seen.append((await reader.get("f_a")).status)

    process.hook = hook
    await _cycle(client, state, config, process, T0)
    assert seen == ["ingesting"]


async def test_pre_routing_failure_retries_then_fails(state, config):
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    process = FakeProcess(outcome="failed")

    await _cycle(client, state, config, process, T0)
    row = await state.get("f_a")
    assert (row.status, row.attempts) == ("waiting_labels", 1)

    await _cycle(client, state, config, process, T0 + minutes(5))
    row = await state.get("f_a")
    assert (row.status, row.attempts) == ("waiting_labels", 2)

    report = await _cycle(client, state, config, process, T0 + minutes(10))
    row = await state.get("f_a")
    assert row.status == "failed"
    assert report.failed == ["f_a"]

    await _cycle(client, state, config, process, T0 + minutes(15))
    assert len(process.calls) == 3


async def test_extraction_failure_not_retried(state, config):
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    process = FakeProcess(outcome="extraction_error")
    await _cycle(client, state, config, process, T0)
    row = await state.get("f_a")
    assert row.status == "failed"
    assert row.error.startswith("extraction: ")
    await _cycle(client, state, config, process, T0 + minutes(5))
    assert len(process.calls) == 1


async def test_per_file_error_isolated(state, config):
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    client.add("f_b", NAMED)
    client.errors[("get_file", "f_a")] = PlaudMcpError("boom")
    process = FakeProcess()
    report = await _cycle(client, state, config, process, T0)
    assert report.ingested == ["f_b"]
    assert (await state.get("f_b")).status == "completed"
    assert await state.get("f_a") is None


async def test_auth_error_propagates_from_cycle(state, config):
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    client.errors[("get_file", "f_a")] = PlaudAuthError("Not logged in")
    with pytest.raises(PlaudAuthError):
        await _cycle(client, state, config, FakeProcess(), T0)


async def test_forever_recovers_interrupted_and_writes_attention(state, config):
    seed_track = TrackState(ready_at=T0, speaker_map=NAMED, last_label_change_at=None)
    await state.upsert_waiting("f_stuck", "Stuck meeting", seed_track, T0)
    await state.mark_ingesting("f_stuck")

    async def stop(_seconds: float) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_plaud_mcp_forever(
            config,
            client_factory=FakePlaudClient,
            process_recording_fn=FakeProcess(),
            sleep=stop,
            clock=lambda: T0,
        )

    row = await state.get("f_stuck")
    assert (row.status, row.error) == ("failed", "interrupted")
    note = (config.pkm_vault_path / ATTENTION_NOTE_REL).read_text()
    assert "f_stuck" in note


@pytest.mark.parametrize("source", ["get_marks", "adapter"])
async def test_marks_or_adapter_failure_uses_attempts_then_fails(
    state, config, monkeypatch, source,
):
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    if source == "get_marks":
        client.errors[("get_marks", "f_a")] = PlaudMcpError("get_transcript timed out")
        expected = "PlaudMcpError: get_transcript timed out"
    else:
        def broken(*_args):
            raise ValueError("highlight marks have no usable time field; keys=['foo']")

        monkeypatch.setattr("automation_daemon.plaud_mcp_ingest.to_recording_job", broken)
        expected = "ValueError: highlight marks have no usable time field; keys=['foo']"
    process = FakeProcess()

    seen = []
    for i in range(3):
        await _cycle(client, state, config, process, T0 + timedelta(minutes=5 * i))
        row = await state.get("f_a")
        seen.append((row.status, row.attempts, row.error))
    assert seen == [
        ("waiting_labels", 1, expected),
        ("waiting_labels", 2, expected),
        ("failed", 3, expected),
    ]
    assert process.calls == []


async def test_marks_auth_error_leaves_no_ingesting_row(state, config):
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    client.errors[("get_marks", "f_a")] = PlaudAuthError("Not logged in")
    process = FakeProcess()
    with pytest.raises(PlaudAuthError):
        await _cycle(client, state, config, process, T0)
    row = await state.get("f_a")
    assert (row.status, row.attempts) == ("waiting_labels", 0)
    assert process.calls == []


class _FlakyState(PlaudMcpStateDB):
    async def upsert_waiting(self, file_id, *args, **kwargs):
        if file_id == "f_a":
            raise RuntimeError("database is locked")
        await super().upsert_waiting(file_id, *args, **kwargs)


@pytest.mark.parametrize("source", ["client", "state"])
async def test_unexpected_item_error_isolated(config, source):
    if source == "state":
        state = _FlakyState(config.email_ingest_state_db_path)
    else:
        state = PlaudMcpStateDB(config.email_ingest_state_db_path)
    await state.init_db()
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    client.add("f_b", NAMED)
    if source == "client":
        client.errors[("get_transcript", "f_a")] = RuntimeError("bad payload")
    process = FakeProcess()
    report = await _cycle(client, state, config, process, T0)
    assert report.ingested == ["f_b"]
    assert (await state.get("f_b")).status == "completed"
    assert list(report.fetch_errors) == ["f_a"]
    assert report.fetch_errors["f_a"].startswith("RuntimeError: ")


async def test_item_missing_id_skipped(state, config):
    client = FakePlaudClient()
    client.add("f_b", NAMED)
    original = client.list_files

    async def listing(date_from, date_to):
        return [{"name": "no id"}, *await original(date_from, date_to)]

    client.list_files = listing
    report = await _cycle(client, state, config, FakeProcess(), T0)
    assert report.ingested == ["f_b"]


async def _run_forever(config, factory, sleep, clock=lambda: T0, process=None):
    with pytest.raises(asyncio.CancelledError):
        await run_plaud_mcp_forever(
            config,
            client_factory=factory,
            process_recording_fn=process or FakeProcess(),
            sleep=sleep,
            clock=clock,
        )


def _note(config):
    path = config.pkm_vault_path / ATTENTION_NOTE_REL
    return path.read_text() if path.exists() else None


def _scripted_factory(script):
    """Return a client factory that raises or yields per scripted step."""
    steps = iter(script)

    def factory():
        step = next(steps)
        if isinstance(step, BaseException):
            raise step
        return step

    return factory


@pytest.mark.parametrize(
    ("exc", "prefix", "hinted"),
    [
        (PlaudAuthError("Not logged in"), "Plaud MCP failing for 3 consecutive polls: ", True),
        (PlaudMcpError("server crashed"), "Plaud MCP failing for 3 consecutive polls: ", False),
        (
            RuntimeError("bad state"),
            "Plaud ingest failing for 3 consecutive polls: RuntimeError: bad state",
            False,
        ),
    ],
    ids=["auth", "mcp", "unexpected"],
)
async def test_forever_problem_threshold_and_reset(config, exc, prefix, hinted):
    factory = _scripted_factory(
        [exc, exc, FakePlaudClient(), exc, exc, exc],
    )
    seen: list[str | None] = []
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        seen.append(_note(config))
        if len(sleeps) >= 6:
            raise asyncio.CancelledError

    await _run_forever(config, factory, sleep)

    assert [n is not None for n in seen] == [False, False, False, False, False, True]
    assert prefix in seen[-1]
    assert ("Plaud login may have expired" in seen[-1]) is hinted
    assert ("on the daemon host." in seen[-1]) is hinted
    assert "Mac mini" not in seen[-1]
    assert sleeps == [config.plaud_mcp_poll_interval_s] * 6


async def test_forever_recovers_interrupted_only_at_startup(state, config):
    seed_track = TrackState(ready_at=T0, speaker_map=NAMED, last_label_change_at=None)
    sleeps: list[float] = []

    async def sleep(_seconds: float) -> None:
        sleeps.append(_seconds)
        if len(sleeps) == 1:
            await state.upsert_waiting("f_live", "Manual run", seed_track, T0)
            await state.mark_ingesting("f_live")
            return
        raise asyncio.CancelledError

    await _run_forever(config, FakePlaudClient, sleep)

    assert len(sleeps) == 2
    row = await state.get("f_live")
    assert (row.status, row.error) == ("ingesting", None)
    assert _note(config) is None


def _scripted_list_failed(monkeypatch, failing_calls: set[int]) -> list[int]:
    calls: list[int] = []
    original = PlaudMcpStateDB.list_failed

    async def list_failed(self):
        calls.append(len(calls) + 1)
        if len(calls) in failing_calls:
            raise RuntimeError("database is locked")
        return await original(self)

    monkeypatch.setattr(PlaudMcpStateDB, "list_failed", list_failed)
    return calls


async def test_forever_attention_failures_below_threshold_not_fatal(config, monkeypatch):
    calls = _scripted_list_failed(monkeypatch, {2, 3, 5, 6})
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 5:
            raise asyncio.CancelledError

    await _run_forever(config, FakePlaudClient, sleep)
    assert len(sleeps) == 5
    assert len(calls) == 6


async def test_forever_raises_after_three_attention_failures(config, monkeypatch):
    written: list[str | None] = []

    def broken_write(_vault, content):
        written.append(content)
        if len(written) >= 2:
            raise OSError("vault unavailable")

    monkeypatch.setattr(
        "automation_daemon.plaud_mcp_ingest.write_attention_note", broken_write,
    )
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 5:
            raise asyncio.CancelledError

    with pytest.raises(RuntimeError, match="attention note") as raised:
        await run_plaud_mcp_forever(
            config,
            client_factory=FakePlaudClient,
            process_recording_fn=FakeProcess(),
            sleep=sleep,
            clock=lambda: T0,
        )
    assert isinstance(raised.value.__cause__, OSError)
    # One good write, then three consecutive failures; no sleep after the third.
    assert len(written) == 4
    assert len(sleeps) == 3


async def test_forever_unwritable_vault_raises_before_polling(config, tmp_path):
    from dataclasses import replace

    missing = replace(config, pkm_vault_path=tmp_path / "not-mounted")
    factory_calls: list[int] = []

    def factory():
        factory_calls.append(1)
        return FakePlaudClient()

    async def sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    with pytest.raises(OSError):
        await run_plaud_mcp_forever(
            missing,
            client_factory=factory,
            process_recording_fn=FakeProcess(),
            sleep=sleep,
            clock=lambda: T0,
        )
    assert factory_calls == []
    assert not (tmp_path / "not-mounted").exists()


async def test_forever_startup_does_not_rewrite_note(config):
    note = config.pkm_vault_path / ATTENTION_NOTE_REL
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("existing problem text\n")
    contents: list[str | None] = []

    def factory():
        contents.append(_note(config))
        return FakePlaudClient()

    async def sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    await _run_forever(config, factory, sleep)
    assert contents == ["existing problem text\n"]


async def test_forever_startup_list_failed_error_raises_before_polling(
    config, monkeypatch,
):
    _scripted_list_failed(monkeypatch, {1})
    factory_calls: list[int] = []

    def factory():
        factory_calls.append(1)
        return FakePlaudClient()

    async def sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    with pytest.raises(RuntimeError, match="database is locked"):
        await run_plaud_mcp_forever(
            config,
            client_factory=factory,
            process_recording_fn=FakeProcess(),
            sleep=sleep,
            clock=lambda: T0,
        )
    assert factory_calls == []


async def test_forever_surfaces_persistent_fetch_errors(config):
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    client.add("f_b", NAMED)
    client.add("f_c", NAMED, ready=False)
    client.errors[("get_file", "f_a")] = PlaudMcpError("boom")
    flaky = PlaudMcpError("flaky")
    client.errors[("get_file", "f_c")] = flaky
    seen: list[str | None] = []

    async def sleep(_seconds: float) -> None:
        seen.append(_note(config))
        if len(seen) == 2:
            del client.errors[("get_file", "f_c")]
        if len(seen) == 3:
            client.errors[("get_file", "f_c")] = flaky
        if len(seen) >= 4:
            raise asyncio.CancelledError

    await _run_forever(config, lambda: client, sleep)

    a_line = "Recording f_a could not be read from Plaud for 3 polls: PlaudMcpError: boom"
    assert [n is not None and a_line in n for n in seen] == [False, False, True, False]
    assert "Recording f_a could not be read from Plaud for 4 polls" in seen[3]
    assert all("f_b" not in n for n in seen if n is not None)
    assert all("f_c" not in n for n in seen if n is not None)


async def test_forever_sleep_subtracts_cycle_time(config):
    current = [T0]
    durations = iter([timedelta(seconds=100), timedelta(seconds=5000)])

    def factory():
        current[0] += next(durations)
        return FakePlaudClient()

    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    await _run_forever(config, factory, sleep, clock=lambda: current[0])
    interval = config.plaud_mcp_poll_interval_s
    assert interval > 100
    assert sleeps == [interval - 100, 0]


async def test_forever_marks_fetch_error_ends_failed_and_listed(config):
    client = FakePlaudClient()
    client.add("f_a", NAMED)
    client.errors[("get_marks", "f_a")] = PlaudMcpError("get_transcript timed out")
    process = FakeProcess()
    seen: list[str | None] = []

    async def sleep(_seconds: float) -> None:
        seen.append(_note(config))
        if len(seen) >= 3:
            raise asyncio.CancelledError

    await _run_forever(config, lambda: client, sleep, process=process)

    row = await PlaudMcpStateDB(config.email_ingest_state_db_path).get("f_a")
    assert (row.status, row.attempts) == ("failed", 3)
    assert seen[2] is not None
    assert "`f_a`" in seen[2] and "get_transcript timed out" in seen[2]
    assert process.calls == []


async def test_stale_unready_recordings_reported(state, config):
    client = FakePlaudClient()
    client.add("f_old", NAMED, name="Old one", ready=False)
    client.add("f_empty", NAMED, name="Empty one", segments=[])
    client.add("f_new", NAMED, name="New one", ready=False)
    client.details["f_new"]["start_at"] = "2026-11-09T09:00:00"
    process = FakeProcess()

    report = await _cycle(client, state, config, process, T0)
    assert report.stale_unready == {}

    report = await _cycle(client, state, config, process, T0 + timedelta(hours=48))
    assert report.stale_unready == {
        "f_old": "Recording f_old (Old one) still has no Plaud transcript/summary after 50 h",
        "f_empty": "Recording f_empty (Empty one) still has no Plaud transcript/summary "
        "after 50 h",
    }
    assert process.calls == []
    assert await state.get("f_old") is None


async def test_forever_writes_stale_unready_line(config):
    client = FakePlaudClient()
    client.add("f_old", NAMED, name="Old one", ready=False)

    async def stop(_seconds: float) -> None:
        raise asyncio.CancelledError

    await _run_forever(config, lambda: client, stop, clock=lambda: T0 + timedelta(hours=49))
    note = _note(config)
    assert note is not None
    assert "Recording f_old (Old one) still has no Plaud transcript/summary after 51 h" in note


_ROUTED = AgentRoutingResult(
    success=True, summary="routed", files_written=["00-Inbox/n.md"],
    summary_path="00-Inbox/n.md",
)
_NOT_ROUTED = AgentRoutingResult(success=False, summary="", error="wrote no files")


@pytest.mark.parametrize(
    "case", ["routed", "routing_error", "write_raises", "capture_config_error"],
)
async def test_real_pipeline_outcomes(state, config, monkeypatch, case):
    written = Path("/vault/04-Archive/t.md")

    def write_raw(*_args, **_kwargs):
        if case == "write_raises":
            raise OSError("disk full")
        return written

    route = AsyncMock(return_value=_NOT_ROUTED if case == "routing_error" else _ROUTED)
    capture = AsyncMock(side_effect=AgentInfraConfigError("node not on PATH"))
    monkeypatch.setattr("automation_daemon.pipeline.write_raw_transcript", write_raw)
    monkeypatch.setattr("automation_daemon.pipeline.agent_extract_and_route", route)
    monkeypatch.setattr("automation_daemon.pipeline.agent_capture_session", capture)
    cfg = dataclasses.replace(
        config, enable_session_capture=case == "capture_config_error",
    )
    client = FakePlaudClient()
    client.add("f_a", NAMED)

    await run_poll_cycle(client, state, cfg, SETTINGS, now=T0)
    row = await state.get("f_a")

    if case in ("routed", "capture_config_error"):
        assert (row.status, row.error) == ("completed", None)
        assert row.transcript_path == str(written)
        assert capture.await_count == (1 if case == "capture_config_error" else 0)
    elif case == "routing_error":
        assert row.status == "failed"
        assert row.error == (
            "extraction: Agent routing failed; transcript preserved: wrote no files"
        )
    else:
        assert (row.status, row.attempts, row.error) == ("waiting_labels", 1, "disk full")
        assert route.await_count == 0

    await run_poll_cycle(client, state, cfg, SETTINGS, now=T0 + minutes(5))
    expected_routes = 0 if case == "write_raises" else 1
    assert route.await_count == expected_routes
