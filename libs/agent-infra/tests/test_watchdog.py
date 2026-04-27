from __future__ import annotations

import asyncio

import pytest

from agent_infra import TraceEvent
from agent_infra.watchdog import (
    AgentInactivityTimeout,
    with_inactivity_watchdog,
)


@pytest.mark.asyncio
async def test_watchdog_passes_through_when_events_flow():
    """If events arrive within the timeout, the work coroutine completes normally."""
    received: list[TraceEvent] = []

    async def on_event(ev: TraceEvent) -> None:
        received.append(ev)

    async def work(emit):
        for i in range(3):
            await emit(TraceEvent(kind="text", content=f"chunk {i}"))
            await asyncio.sleep(0.02)
        return "done"

    result = await with_inactivity_watchdog(
        work, on_event=on_event,
        inactivity_timeout_s=1.0, poll_interval_s=0.05,
    )
    assert result == "done"
    assert len(received) == 3


@pytest.mark.asyncio
async def test_watchdog_cancels_when_no_events_arrive():
    """If no event arrives for inactivity_timeout_s, raise AgentInactivityTimeout."""

    async def silent_work(emit):
        await asyncio.sleep(10)
        return "should not reach"

    with pytest.raises(AgentInactivityTimeout):
        await with_inactivity_watchdog(
            silent_work, on_event=None,
            inactivity_timeout_s=0.2, poll_interval_s=0.05,
        )


@pytest.mark.asyncio
async def test_watchdog_resets_on_each_event():
    """Events arriving regularly keep the watchdog from firing even past timeout."""
    completed = False

    async def slow_steady(emit):
        nonlocal completed
        # Each step takes 0.1s; total run = 0.5s, longer than timeout=0.3s.
        # But events arrive every 0.1s, so the inactivity timer keeps resetting.
        for _ in range(5):
            await emit(TraceEvent(kind="text", content="tick"))
            await asyncio.sleep(0.1)
        completed = True
        return "ok"

    result = await with_inactivity_watchdog(
        slow_steady, on_event=None,
        inactivity_timeout_s=0.3, poll_interval_s=0.02,
    )
    assert completed
    assert result == "ok"


@pytest.mark.asyncio
async def test_watchdog_propagates_work_exception():
    """If work raises, AgentInactivityTimeout is NOT raised; the original is."""

    class BoomError(Exception):
        pass

    async def boom(emit):
        await emit(TraceEvent(kind="text", content="hi"))
        raise BoomError("kaboom")

    with pytest.raises(BoomError):
        await with_inactivity_watchdog(
            boom, on_event=None,
            inactivity_timeout_s=1.0, poll_interval_s=0.02,
        )
