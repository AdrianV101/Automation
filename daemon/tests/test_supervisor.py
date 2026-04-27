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
        await real_sleep(60)  # call 4+: block until cancelled

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

    assert calls >= 4, f"expected at least 4 calls, got {calls}"
    # Call 1 crashes (unstable): sleep = 0.02 (initial backoff), then double to 0.04.
    # Call 2 runs 0.1s then crashes (stable): sleep = 0.04 (accumulated backoff),
    #   then reset to 0.02 — we still pay the accumulated penalty once, but the
    #   next restart gets a fresh start.
    # Call 3 crashes (unstable, after reset): sleep = 0.02 (confirms reset worked).
    assert supervisor_sleeps[0] == 0.02, f"first sleep should be 0.02, got {supervisor_sleeps[0]}"
    assert supervisor_sleeps[1] == 0.04, (
        f"sleep after stable run should still use accumulated backoff (0.04), "
        f"got {supervisor_sleeps[1]}"
    )
    assert supervisor_sleeps[2] == 0.02, (
        f"sleep after post-stable-reset crash should use reset backoff (0.02), "
        f"got {supervisor_sleeps[2]}"
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
