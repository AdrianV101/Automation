"""Inactivity watchdog for streaming agent invocations.

Wraps a work coroutine that emits TraceEvent objects via an `on_event` callback.
If no event arrives for `inactivity_timeout_s` seconds, the work is cancelled
and AgentInactivityTimeout is raised. This protects the daemon from agents that
hang waiting on a dead MCP server or a stalled subprocess pipe.

The watchdog times INACTIVITY (gap between events), not total wall-clock — a
long-running agent that emits any TraceEvent (text chunk, tool_start,
tool_result, complete, error) will keep the timer fresh.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .events import TraceEvent

log = logging.getLogger(__name__)

T = TypeVar("T")


class AgentInactivityTimeout(Exception):
    """Raised when no TraceEvent arrives within the inactivity timeout."""


async def with_inactivity_watchdog(
    work: Callable[[Callable[[TraceEvent], Awaitable[None]]], Awaitable[T]],
    *,
    on_event: Callable[[TraceEvent], Awaitable[None]] | None,
    inactivity_timeout_s: float,
    poll_interval_s: float = 5.0,
) -> T:
    """Run `work(emit)` and cancel it if `emit` is silent for too long.

    work                  : factory that takes an `emit(event)` callable and runs
                            the streamed invocation
    on_event              : optional user callback; called from inside the wrapped
                            emit so the user-visible event stream is preserved
    inactivity_timeout_s  : raise AgentInactivityTimeout if no event for this long
    poll_interval_s       : how often the watchdog checks (small enough to react
                            promptly, large enough to be cheap)
    """
    last_event_at = time.monotonic()
    lock = asyncio.Lock()

    async def emit(event: TraceEvent) -> None:
        nonlocal last_event_at
        async with lock:
            last_event_at = time.monotonic()
        if on_event is not None:
            await on_event(event)

    work_task: asyncio.Task[T] = asyncio.create_task(work(emit))

    async def watchdog() -> None:
        while True:
            await asyncio.sleep(poll_interval_s)
            async with lock:
                idle_for = time.monotonic() - last_event_at
            if idle_for > inactivity_timeout_s:
                # Log on detection — if the drain below hangs, this is the
                # only signal an operator gets that the watchdog tripped.
                log.warning(
                    "Inactivity watchdog tripped at %.1fs idle, cancelling work",
                    idle_for,
                )
                return

    watchdog_task: asyncio.Task[None] = asyncio.create_task(watchdog())

    try:
        done, _pending = await asyncio.wait(
            {work_task, watchdog_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        if not work_task.done():
            work_task.cancel()
        if not watchdog_task.done():
            watchdog_task.cancel()

    # Honor outer cancellation if it landed during asyncio.wait. asyncio.wait
    # can return normally even when the awaiting task was cancelled, so check
    # explicitly and propagate before raising any synthetic timeout.
    current = asyncio.current_task()
    if current is not None and current.cancelling() > 0:
        raise asyncio.CancelledError()

    if work_task in done:
        return work_task.result()

    # Watchdog fired first — drain the cancelled work_task. If the work raised
    # a real exception (e.g., an SDK error that itself caused the silence), log
    # it: the AgentInactivityTimeout we raise next would otherwise mask it.
    try:
        await work_task
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception(
            "Work task raised during inactivity-watchdog drain "
            "(masked by AgentInactivityTimeout)",
        )
    raise AgentInactivityTimeout(
        f"No TraceEvent for {inactivity_timeout_s:.1f}s",
    )
