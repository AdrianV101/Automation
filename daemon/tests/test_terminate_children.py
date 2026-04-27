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


def test_terminate_children_swallows_access_denied_during_kill():
    """A SIGKILL that fails with AccessDenied is logged-and-skipped, not
    propagated — daemon shutdown must not crash on cleanup edge cases."""
    c1 = _make_child(101)
    c1.kill.side_effect = psutil.AccessDenied(pid=101)
    me = MagicMock(spec=psutil.Process)
    me.children.return_value = [c1]

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
