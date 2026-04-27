"""Supervised task wrapper with exponential backoff restart.

A supervisor calls a coroutine factory in a loop; if the factory raises any
non-CancelledError exception, it logs and restarts after a backoff. Cancellation
propagates so the parent TaskGroup can shut down cleanly.

Limitation: this catches crashes (factory raised) and clean returns (factory
returned without raising). It does NOT detect a task that is silently parked
on a non-firing await — for that, use a watchdog at the call site (e.g.,
agent_infra.watchdog.with_inactivity_watchdog) or a heartbeat-based liveness
check inside the supervised work.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)


async def supervise(
    name: str,
    factory: Callable[[], Awaitable[None]],
    *,
    restart_backoff_s: float = 5.0,
    max_backoff_s: float = 60.0,
    stable_after_s: float = 60.0,
) -> None:
    """Run `factory()` in a loop; restart on failure with exponential backoff.

    name              : log identifier for this task
    factory           : zero-arg async callable that runs the work
    restart_backoff_s : initial sleep between restarts after a failure
    max_backoff_s     : cap on the backoff sleep
    stable_after_s    : if a run exceeds this duration, reset the backoff
    """
    backoff = restart_backoff_s
    while True:
        started = time.monotonic()
        try:
            await factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            elapsed = time.monotonic() - started
            log.exception("%s crashed after %.1fs, restarting in %.1fs", name, elapsed, backoff)
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise
            if elapsed >= stable_after_s:
                backoff = restart_backoff_s
            else:
                backoff = min(backoff * 2, max_backoff_s)
            continue
        # Factory returned cleanly — restart with the same backoff logic as
        # the exception path. Long-running tasks aren't supposed to return.
        elapsed = time.monotonic() - started
        log.warning("%s returned after %.1fs (expected long-running), restarting in %.1fs", name, elapsed, backoff)
        try:
            await asyncio.sleep(backoff)
        except asyncio.CancelledError:
            raise
        if elapsed >= stable_after_s:
            backoff = restart_backoff_s
        else:
            backoff = min(backoff * 2, max_backoff_s)
