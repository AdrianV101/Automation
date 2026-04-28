from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest

from audio_ingest.supervisor import supervise


@pytest.mark.asyncio
async def test_supervise_restarts_after_factory_raises(caplog):
    caplog.set_level(logging.WARNING)
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError(f"crash {calls}")
        await asyncio.sleep(0.05)

    task = asyncio.create_task(
        supervise("flaky", flaky, restart_backoff_s=0.01, max_backoff_s=0.05),
    )
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls >= 3
    assert any("flaky crashed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_supervise_propagates_cancellation():
    started = asyncio.Event()

    async def long_running():
        started.set()
        await asyncio.sleep(60)

    task = asyncio.create_task(
        supervise("long_running", long_running, restart_backoff_s=0.01),
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_supervise_resets_backoff_after_long_run():
    """Stable runs reset the backoff: after a 0.1s run that crashes, the
    next sleep should be restart_backoff_s, not doubled."""
    sleep_durations: list[float] = []
    calls = 0

    real_sleep = asyncio.sleep

    async def tracking_sleep(duration: float) -> None:
        sleep_durations.append(duration)
        await real_sleep(duration)

    async def crash_then_stable_then_crash():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("first crash (unstable)")
        if calls == 2:
            await real_sleep(0.1)  # > stable_after_s; use real_sleep to avoid intercepting
            raise RuntimeError("second crash (after stable run)")
        if calls == 3:
            raise RuntimeError("third crash (unstable, after reset)")
        if calls == 4:
            raise RuntimeError("fourth crash (unstable, doubling resumes)")
        await real_sleep(60)  # call 5+: block until cancelled

    with patch("audio_ingest.supervisor.asyncio.sleep", side_effect=tracking_sleep):
        task = asyncio.create_task(
            supervise(
                "stable_then_crash", crash_then_stable_then_crash,
                restart_backoff_s=0.02, max_backoff_s=1.0, stable_after_s=0.05,
            ),
        )
        await real_sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    # sleep_durations only contains supervisor backoff sleeps because the factory
    # uses real_sleep directly, bypassing the patch.
    supervisor_sleeps = sleep_durations

    assert calls >= 5, f"expected at least 5 calls, got {calls}"
    # Call 1 crashes (unstable): sleep = 0.02 (initial), backoff doubles to 0.04.
    # Call 2 runs 0.1s then crashes (stable): reset to 0.02 BEFORE sleeping;
    #   no double-after since elapsed >= stable_after_s. Backoff stays 0.02.
    # Call 3 crashes (unstable): sleep = 0.02 (proves reset took effect),
    #   then doubles to 0.04.
    # Call 4 crashes (unstable): sleep = 0.04 (proves doubling resumed).
    assert supervisor_sleeps[0] == 0.02, f"first sleep should be 0.02, got {supervisor_sleeps[0]}"
    assert supervisor_sleeps[1] == 0.02, (
        f"sleep after stable run should reset to restart_backoff_s (0.02), "
        f"got {supervisor_sleeps[1]}"
    )
    assert supervisor_sleeps[2] == 0.02, (
        f"first post-reset sleep should still be 0.02, got {supervisor_sleeps[2]}"
    )
    assert supervisor_sleeps[3] == 0.04, (
        f"second post-reset unstable crash should sleep doubled (0.04), "
        f"got {supervisor_sleeps[3]}"
    )


@pytest.mark.asyncio
async def test_supervise_fires_persistent_failure_after_threshold():
    """After N consecutive non-stable crashes, on_persistent_failure is
    fired once with the task name and failure count."""
    alerts: list[tuple[str, int]] = []

    async def alert(name: str, failures: int) -> None:
        alerts.append((name, failures))

    async def always_crash():
        raise RuntimeError("boom")

    task = asyncio.create_task(
        supervise(
            "crashy", always_crash,
            restart_backoff_s=0.01, max_backoff_s=0.05,
            persistent_failure_threshold=3,
            on_persistent_failure=alert,
        ),
    )
    # Let the supervisor run long enough for >3 crash cycles
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The callback fires exactly once after threshold reached
    assert len(alerts) == 1, f"expected 1 alert, got {len(alerts)}: {alerts}"
    name, count = alerts[0]
    assert name == "crashy"
    assert count >= 3


@pytest.mark.asyncio
async def test_supervise_persistent_failure_resets_after_stable_run():
    """A stable run between crash sequences re-arms the alert: it can fire
    again on the next sustained crash loop."""
    alerts: list[tuple[str, int]] = []
    calls = 0

    async def alert(name: str, failures: int) -> None:
        alerts.append((name, failures))

    async def crash_then_stable_then_crash():
        nonlocal calls
        calls += 1
        # First 3 calls crash immediately (triggers first alert)
        if calls <= 3:
            raise RuntimeError(f"crash {calls}")
        # Call 4 runs stably
        if calls == 4:
            await asyncio.sleep(0.1)
            return
        # Calls 5+ crash again (should re-trigger alert after enough crashes)
        raise RuntimeError(f"crash {calls}")

    task = asyncio.create_task(
        supervise(
            "flaky", crash_then_stable_then_crash,
            restart_backoff_s=0.01, max_backoff_s=0.05,
            stable_after_s=0.05,
            persistent_failure_threshold=3,
            on_persistent_failure=alert,
        ),
    )
    await asyncio.sleep(0.6)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Two alerts: one for the first crash sequence, one after the stable
    # run rearmed the counter and three more crashes accumulated.
    assert len(alerts) == 2, f"expected 2 alerts, got {len(alerts)}: {alerts}"


@pytest.mark.asyncio
async def test_supervise_persistent_failure_callback_exception_does_not_crash_supervisor(caplog):
    """A buggy on_persistent_failure callback must not take the supervisor
    down — log it and keep restarting the work."""
    caplog.set_level(logging.ERROR)

    async def bad_alert(name: str, failures: int) -> None:
        raise RuntimeError("alert callback bug")

    async def always_crash():
        raise RuntimeError("crash")

    task = asyncio.create_task(
        supervise(
            "crashy", always_crash,
            restart_backoff_s=0.01,
            persistent_failure_threshold=2,
            on_persistent_failure=bad_alert,
        ),
    )
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Supervisor logged the callback exception and kept running
    assert any(
        "on_persistent_failure raised for crashy" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_supervise_restarts_after_factory_returns_cleanly(caplog):
    """Long-running factories that return cleanly should be restarted
    with a warning log."""
    caplog.set_level(logging.WARNING)
    calls = 0

    async def returns_immediately():
        nonlocal calls
        calls += 1

    task = asyncio.create_task(
        supervise(
            "returner", returns_immediately,
            restart_backoff_s=0.01, max_backoff_s=0.05,
        ),
    )
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls >= 2, f"expected at least 2 restarts, got {calls}"
    assert any(
        "returned after" in r.message and "returner" in r.message
        for r in caplog.records
    ), "expected warning log mentioning the task name and 'returned after'"
