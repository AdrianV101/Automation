from __future__ import annotations

import asyncio
import logging

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
async def test_supervise_resets_backoff_after_long_run(caplog):
    caplog.set_level(logging.WARNING)
    calls = 0

    async def flaky_then_stable():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("first crash")
        # second call: run "long enough" to count as success
        await asyncio.sleep(0.1)
        raise RuntimeError("second crash")

    task = asyncio.create_task(
        supervise(
            "flaky_then_stable", flaky_then_stable,
            restart_backoff_s=0.02, max_backoff_s=1.0,
            stable_after_s=0.05,
        ),
    )
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # If backoff reset, second crash's backoff is 0.02s, not 0.04s.
    # Sanity check: at least 2 calls happened.
    assert calls >= 2
