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
    persistent_failure_threshold: int = 3,
    on_persistent_failure: Callable[[str, int], Awaitable[None]] | None = None,
) -> None:
    """Run `factory()` in a loop; restart on failure with exponential backoff.

    Mirrors the IMAP listener's persistent-failure pattern: after
    `persistent_failure_threshold` consecutive non-stable failures, fires
    `on_persistent_failure(name, consecutive_failures)` once. The callback
    is fired again only after a stable run resets the counter.

    name                          : log identifier for this task
    factory                       : zero-arg async callable that runs the work
    restart_backoff_s             : initial sleep between restarts after a failure
    max_backoff_s                 : cap on the backoff sleep
    stable_after_s                : if a run exceeds this duration, reset the backoff
    persistent_failure_threshold  : after this many consecutive non-stable
                                    failures, fire on_persistent_failure
    on_persistent_failure         : one-shot alert callback when crash-looping
    """
    backoff = restart_backoff_s
    consecutive_failures = 0
    persistent_alerted = False

    async def _fire_persistent(failures: int) -> None:
        nonlocal persistent_alerted
        if persistent_alerted or on_persistent_failure is None:
            return
        persistent_alerted = True
        try:
            await on_persistent_failure(name, failures)
        except Exception:
            log.exception("on_persistent_failure raised for %s", name)

    while True:
        started = time.monotonic()
        try:
            await factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            elapsed = time.monotonic() - started
            if elapsed >= stable_after_s:
                # Stable run before crashing — reset both backoff and the
                # persistent-failure counter (so the next crash-loop alerts).
                backoff = restart_backoff_s
                consecutive_failures = 0
                persistent_alerted = False
            consecutive_failures += 1
            log.exception(
                "%s crashed after %.1fs (consecutive failures=%d), restarting in %.1fs",
                name, elapsed, consecutive_failures, backoff,
            )
            if consecutive_failures >= persistent_failure_threshold:
                await _fire_persistent(consecutive_failures)
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise
            if elapsed < stable_after_s:
                backoff = min(backoff * 2, max_backoff_s)
            continue
        elapsed = time.monotonic() - started
        if elapsed >= stable_after_s:
            backoff = restart_backoff_s
            consecutive_failures = 0
            persistent_alerted = False
        log.warning("%s returned after %.1fs (expected long-running), restarting in %.1fs", name, elapsed, backoff)
        try:
            await asyncio.sleep(backoff)
        except asyncio.CancelledError:
            raise
        if elapsed < stable_after_s:
            backoff = min(backoff * 2, max_backoff_s)
