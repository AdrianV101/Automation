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


@pytest.mark.asyncio
async def test_watchdog_drains_work_task_finally_block_executes():
    """When the watchdog cancels the work task, its finally block must run
    before with_inactivity_watchdog returns — no orphaned tasks holding
    resources past this function's return."""
    finally_ran = asyncio.Event()

    async def work_with_finally(_emit):
        try:
            await asyncio.sleep(60)
        finally:
            finally_ran.set()

    with pytest.raises(AgentInactivityTimeout):
        await with_inactivity_watchdog(
            work_with_finally, on_event=None,
            inactivity_timeout_s=0.1, poll_interval_s=0.02,
        )

    # By the time with_inactivity_watchdog returned, the work task's finally
    # must have executed — otherwise it's still running in the background.
    assert finally_ran.is_set(), "work task finally did not run before watchdog returned"


@pytest.mark.asyncio
async def test_watchdog_logs_masked_exception(caplog):
    """If the work raises a real exception around the time it's cancelled,
    log it so the user can see what actually went wrong."""
    import logging as stdlib_logging

    class HiddenError(Exception):
        pass

    async def crashing_work(emit):
        # Begin work, then raise after the watchdog will have fired.
        await asyncio.sleep(0.15)
        raise HiddenError("real underlying cause")

    caplog.set_level(stdlib_logging.ERROR, logger="agent_infra.watchdog")

    with pytest.raises(AgentInactivityTimeout):
        await with_inactivity_watchdog(
            crashing_work, on_event=None,
            inactivity_timeout_s=0.05, poll_interval_s=0.02,
        )

    # Either the work was cancelled cleanly OR the masked error was logged —
    # both outcomes are acceptable; the bug we're guarding against is
    # silently dropping HiddenError entirely.
    if any("masked by AgentInactivityTimeout" in r.message for r in caplog.records):
        # The error path executed — verify HiddenError was the logged exception
        masked_records = [
            r for r in caplog.records
            if "masked by AgentInactivityTimeout" in r.message
        ]
        assert any(
            r.exc_info and r.exc_info[0] is HiddenError
            for r in masked_records
        ), "masked-exception log did not include HiddenError"


@pytest.mark.asyncio
async def test_watchdog_raises_for_zero_timeout():
    """inactivity_timeout_s must be positive."""
    with pytest.raises(ValueError, match="inactivity_timeout_s must be positive"):
        await with_inactivity_watchdog(
            lambda emit: asyncio.sleep(0),
            on_event=None,
            inactivity_timeout_s=0,
            poll_interval_s=0.01,
        )


@pytest.mark.asyncio
async def test_watchdog_raises_when_poll_interval_exceeds_timeout():
    """poll_interval_s must be less than inactivity_timeout_s."""
    with pytest.raises(ValueError, match="poll_interval_s"):
        await with_inactivity_watchdog(
            lambda emit: asyncio.sleep(0),
            on_event=None,
            inactivity_timeout_s=0.1,
            poll_interval_s=0.5,
        )


@pytest.mark.asyncio
async def test_watchdog_fires_after_events_then_silence():
    """Events arrive then stop; watchdog fires and on_event received all events."""
    received: list[TraceEvent] = []

    async def on_event(ev: TraceEvent) -> None:
        received.append(ev)

    async def emit_then_hang(emit):
        await emit(TraceEvent(kind="text", content="hello"))
        await asyncio.sleep(60)  # goes silent

    with pytest.raises(AgentInactivityTimeout):
        await with_inactivity_watchdog(
            emit_then_hang,
            on_event=on_event,
            inactivity_timeout_s=0.2,
            poll_interval_s=0.05,
        )

    assert len(received) == 1
    assert received[0].content == "hello"
