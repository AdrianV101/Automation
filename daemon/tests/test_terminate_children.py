"""Tests for _terminate_children — subprocess cleanup at daemon shutdown.

There's no clean way to test the SIGTERM-then-SIGKILL escalation against
real subprocesses without spawning real Claude SDK clients, so these tests
mock psutil and assert the call sequence on the descendants the function
finds.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import psutil
import pytest

from automation_daemon.__main__ import _terminate_children, main


def _make_child(pid: int, name: str = "claude") -> MagicMock:
    child = MagicMock(spec=psutil.Process)
    child.pid = pid
    child.name.return_value = name
    return child


def test_terminate_children_no_children_is_noop():
    """When the daemon has no descendants, _terminate_children returns
    quickly without calling psutil.wait_procs."""
    me = MagicMock(spec=psutil.Process)
    me.children.return_value = []

    with (
        patch("automation_daemon.__main__.psutil.Process", return_value=me),
        patch("automation_daemon.__main__.psutil.wait_procs") as mock_wait,
    ):
        _terminate_children()

    me.children.assert_called_once_with(recursive=True)
    mock_wait.assert_not_called()


def test_terminate_children_sigterms_all_then_returns_when_all_exit():
    """SIGTERM is sent to every child; if they all exit before the timeout,
    SIGKILL is never invoked."""
    c1 = _make_child(101)
    c2 = _make_child(102)
    me = MagicMock(spec=psutil.Process)
    me.children.return_value = [c1, c2]

    with (
        patch("automation_daemon.__main__.psutil.Process", return_value=me),
        patch(
            "automation_daemon.__main__.psutil.wait_procs",
            return_value=([c1, c2], []),
        ) as mock_wait,
    ):
        _terminate_children(timeout_s=1.0)

    c1.terminate.assert_called_once()
    c2.terminate.assert_called_once()
    mock_wait.assert_called_once_with([c1, c2], timeout=1.0)
    c1.kill.assert_not_called()
    c2.kill.assert_not_called()


def test_terminate_children_sigkills_survivors_after_timeout():
    """If wait_procs returns survivors in `alive`, SIGKILL is sent to them."""
    c1 = _make_child(101)
    c2 = _make_child(102)  # survives the SIGTERM grace period
    me = MagicMock(spec=psutil.Process)
    me.children.return_value = [c1, c2]

    with (
        patch("automation_daemon.__main__.psutil.Process", return_value=me),
        patch(
            "automation_daemon.__main__.psutil.wait_procs",
            return_value=([c1], [c2]),
        ),
    ):
        _terminate_children(timeout_s=0.5)

    c1.terminate.assert_called_once()
    c2.terminate.assert_called_once()
    c1.kill.assert_not_called()  # already exited
    c2.kill.assert_called_once()  # forced


def test_terminate_children_swallows_no_such_process_during_terminate():
    """A child that exits between children() and terminate() raises
    NoSuchProcess; we swallow it and continue with the rest."""
    c1 = _make_child(101)
    c1.terminate.side_effect = psutil.NoSuchProcess(pid=101)
    c2 = _make_child(102)
    me = MagicMock(spec=psutil.Process)
    me.children.return_value = [c1, c2]

    with (
        patch("automation_daemon.__main__.psutil.Process", return_value=me),
        patch(
            "automation_daemon.__main__.psutil.wait_procs",
            return_value=([c1, c2], []),
        ),
    ):
        # Must not raise
        _terminate_children(timeout_s=0.5)

    c1.terminate.assert_called_once()
    c2.terminate.assert_called_once()


def test_terminate_children_logs_access_denied_during_kill(caplog):
    """A SIGKILL that fails with AccessDenied is logged at WARNING and
    skipped; daemon shutdown must not crash on cleanup edge cases, but
    leaving an orphan running deserves an operator-visible signal."""
    c1 = _make_child(101)
    c1.kill.side_effect = psutil.AccessDenied(pid=101)
    me = MagicMock(spec=psutil.Process)
    me.children.return_value = [c1]

    caplog.set_level(logging.WARNING, logger="automation_daemon.__main__")

    with (
        patch("automation_daemon.__main__.psutil.Process", return_value=me),
        patch(
            "automation_daemon.__main__.psutil.wait_procs",
            return_value=([], [c1]),
        ),
    ):
        _terminate_children(timeout_s=0.5)

    c1.terminate.assert_called_once()
    c1.kill.assert_called_once()
    assert any(
        "AccessDenied killing" in r.message and "pid=101" in r.message
        for r in caplog.records
    ), f"missing AccessDenied warning; got: {[r.message for r in caplog.records]}"


def test_terminate_children_logs_access_denied_during_terminate(caplog):
    """SIGTERM AccessDenied is also logged at WARNING."""
    c1 = _make_child(101)
    c1.terminate.side_effect = psutil.AccessDenied(pid=101)
    me = MagicMock(spec=psutil.Process)
    me.children.return_value = [c1]

    caplog.set_level(logging.WARNING, logger="automation_daemon.__main__")

    with (
        patch("automation_daemon.__main__.psutil.Process", return_value=me),
        patch(
            "automation_daemon.__main__.psutil.wait_procs",
            return_value=([c1], []),
        ),
    ):
        _terminate_children(timeout_s=0.5)

    assert any(
        "AccessDenied terminating" in r.message and "pid=101" in r.message
        for r in caplog.records
    ), f"missing AccessDenied warning; got: {[r.message for r in caplog.records]}"


def test_terminate_children_force_kills_when_wait_procs_raises(caplog):
    """If wait_procs itself raises, the function logs and falls back to
    SIGKILLing all children — no crash, no leaked subprocesses."""
    c1 = _make_child(101)
    c2 = _make_child(102)
    me = MagicMock(spec=psutil.Process)
    me.children.return_value = [c1, c2]

    caplog.set_level(logging.ERROR, logger="automation_daemon.__main__")

    with (
        patch("automation_daemon.__main__.psutil.Process", return_value=me),
        patch(
            "automation_daemon.__main__.psutil.wait_procs",
            side_effect=psutil.Error("simulated wait_procs failure"),
        ),
    ):
        _terminate_children(timeout_s=0.5)

    c1.terminate.assert_called_once()
    c2.terminate.assert_called_once()
    # All children get SIGKILL since wait_procs couldn't enumerate survivors
    c1.kill.assert_called_once()
    c2.kill.assert_called_once()
    assert any(
        "wait_procs raised" in r.message for r in caplog.records
    )


def test_terminate_children_logs_count_at_info_level(caplog):
    """Operators reading shutdown logs should see how many descendants we
    cleaned up."""
    children = [_make_child(100 + i) for i in range(3)]
    me = MagicMock(spec=psutil.Process)
    me.children.return_value = children

    with (
        patch("automation_daemon.__main__.psutil.Process", return_value=me),
        patch(
            "automation_daemon.__main__.psutil.wait_procs",
            return_value=(children, []),
        ),
    ):
        caplog.set_level(logging.INFO, logger="automation_daemon.__main__")
        _terminate_children(timeout_s=0.5)

    messages = [r.message for r in caplog.records if r.name == "automation_daemon.__main__"]
    assert any("Terminating 3 child" in m for m in messages), (
        f"missing termination log; got: {messages}"
    )


def test_terminate_children_name_raises_no_such_process_in_terminate_handler(caplog):
    """If c.name() raises NoSuchProcess inside the AccessDenied-terminate handler,
    the loop must not unwind — the remaining children still get processed."""
    c1 = _make_child(101)
    c1.terminate.side_effect = psutil.AccessDenied(pid=101)
    # name() raises NoSuchProcess (process vanished between terminate and log)
    c1.name.side_effect = psutil.NoSuchProcess(pid=101)

    c2 = _make_child(102)

    me = MagicMock(spec=psutil.Process)
    me.children.return_value = [c1, c2]

    caplog.set_level(logging.WARNING, logger="automation_daemon.__main__")

    with (
        patch("automation_daemon.__main__.psutil.Process", return_value=me),
        patch(
            "automation_daemon.__main__.psutil.wait_procs",
            return_value=([c1, c2], []),
        ),
    ):
        # Must not raise; c2 must still be processed
        _terminate_children(timeout_s=0.5)

    c2.terminate.assert_called_once()
    # Warning log should use <unknown> for the name
    assert any(
        "AccessDenied terminating" in r.message
        and "pid=101" in r.message
        and "<unknown>" in r.message
        for r in caplog.records
    ), f"expected AccessDenied terminating warning with <unknown>; got: {[r.message for r in caplog.records]}"


def test_terminate_children_name_raises_no_such_process_in_kill_handler(caplog):
    """If c.name() raises NoSuchProcess inside the AccessDenied-kill handler,
    the loop must not unwind — remaining alive children still get SIGKILL."""
    c1 = _make_child(101)
    c1.kill.side_effect = psutil.AccessDenied(pid=101)
    c1.name.side_effect = psutil.NoSuchProcess(pid=101)

    c2 = _make_child(102)

    me = MagicMock(spec=psutil.Process)
    me.children.return_value = [c1, c2]

    caplog.set_level(logging.WARNING, logger="automation_daemon.__main__")

    with (
        patch("automation_daemon.__main__.psutil.Process", return_value=me),
        patch(
            "automation_daemon.__main__.psutil.wait_procs",
            return_value=([], [c1, c2]),
        ),
    ):
        _terminate_children(timeout_s=0.5)

    c2.kill.assert_called_once()
    assert any(
        "AccessDenied killing" in r.message
        and "pid=101" in r.message
        and "<unknown>" in r.message
        for r in caplog.records
    ), f"expected AccessDenied killing warning with <unknown>; got: {[r.message for r in caplog.records]}"


def test_main_shutdown_asyncgens_timeout_logs_warning_and_calls_terminate_children(caplog):
    """If shutdown_asyncgens hangs past 10 s, main() logs a warning at WARNING
    level and proceeds to _terminate_children rather than blocking forever."""

    async def _hanging_asyncgens():
        await asyncio.sleep(9999)

    caplog.set_level(logging.WARNING, logger="automation_daemon.__main__")

    # Patch DaemonConfig.from_env and argparse so main() reaches the loop path
    mock_config = MagicMock()
    mock_args = MagicMock()
    mock_args.command = None  # trigger the daemon path, not a subcommand

    # run_daemon must return an awaitable that completes immediately
    async def _noop_run_daemon(_config):
        return

    with (
        patch("automation_daemon.__main__.DaemonConfig.from_env", return_value=mock_config),
        patch("automation_daemon.__main__.argparse.ArgumentParser") as mock_parser_cls,
        patch("automation_daemon.__main__.run_daemon", side_effect=_noop_run_daemon),
        patch("automation_daemon.__main__.loop") if False else patch("automation_daemon.__main__._install_signal_handlers"),
        patch("automation_daemon.__main__._terminate_children") as mock_terminate,
    ):
        mock_parser = MagicMock()
        mock_parser_cls.return_value = mock_parser
        mock_parser.parse_args.return_value = mock_args
        mock_parser.add_subparsers.return_value = MagicMock()

        # Replace shutdown_asyncgens with a coroutine that always times out
        real_new_event_loop = asyncio.new_event_loop

        def _patched_new_event_loop():
            loop = real_new_event_loop()
            loop.shutdown_asyncgens = _hanging_asyncgens
            return loop

        with patch("automation_daemon.__main__.asyncio.new_event_loop", side_effect=_patched_new_event_loop):
            main()

    mock_terminate.assert_called_once()
    assert any(
        "shutdown_asyncgens timed out" in r.message for r in caplog.records
    ), f"expected timeout warning; got: {[r.message for r in caplog.records]}"
