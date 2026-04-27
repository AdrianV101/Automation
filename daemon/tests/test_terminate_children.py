"""Tests for _terminate_children — subprocess cleanup at daemon shutdown.

There's no clean way to test the SIGTERM-then-SIGKILL escalation against
real subprocesses without spawning real Claude SDK clients, so these tests
mock psutil and assert the call sequence on the descendants the function
finds.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import psutil
import pytest

from audio_ingest.__main__ import _terminate_children


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
        patch("audio_ingest.__main__.psutil.Process", return_value=me),
        patch("audio_ingest.__main__.psutil.wait_procs") as mock_wait,
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
        patch("audio_ingest.__main__.psutil.Process", return_value=me),
        patch(
            "audio_ingest.__main__.psutil.wait_procs",
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
        patch("audio_ingest.__main__.psutil.Process", return_value=me),
        patch(
            "audio_ingest.__main__.psutil.wait_procs",
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
        patch("audio_ingest.__main__.psutil.Process", return_value=me),
        patch(
            "audio_ingest.__main__.psutil.wait_procs",
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

    caplog.set_level(logging.WARNING, logger="audio_ingest.__main__")

    with (
        patch("audio_ingest.__main__.psutil.Process", return_value=me),
        patch(
            "audio_ingest.__main__.psutil.wait_procs",
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

    caplog.set_level(logging.WARNING, logger="audio_ingest.__main__")

    with (
        patch("audio_ingest.__main__.psutil.Process", return_value=me),
        patch(
            "audio_ingest.__main__.psutil.wait_procs",
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

    caplog.set_level(logging.ERROR, logger="audio_ingest.__main__")

    with (
        patch("audio_ingest.__main__.psutil.Process", return_value=me),
        patch(
            "audio_ingest.__main__.psutil.wait_procs",
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
        patch("audio_ingest.__main__.psutil.Process", return_value=me),
        patch(
            "audio_ingest.__main__.psutil.wait_procs",
            return_value=(children, []),
        ),
    ):
        caplog.set_level(logging.INFO, logger="audio_ingest.__main__")
        _terminate_children(timeout_s=0.5)

    messages = [r.message for r in caplog.records if r.name == "audio_ingest.__main__"]
    assert any("Terminating 3 child" in m for m in messages), (
        f"missing termination log; got: {messages}"
    )
