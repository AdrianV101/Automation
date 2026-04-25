from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from .events import TraceEvent
from .results import AgentLoopResult
from .utils import extract_file_path

log = logging.getLogger(__name__)


async def run_agent_loop(
    user_prompt: str, options: ClaudeAgentOptions,
) -> AgentLoopResult:
    """Run the Agent SDK query loop and collect text/file results."""
    result = AgentLoopResult()

    try:
        async for message in query(prompt=user_prompt, options=options):
            if isinstance(message, AssistantMessage):
                result.turns_used += 1
                for block in message.content:
                    if isinstance(block, TextBlock):
                        result.text_parts.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        path = extract_file_path(block.name, block.input)
                        if path and path not in result.files_written:
                            result.files_written.append(path)
    except Exception:
        log.exception("Agent SDK query failed")
        result.error = "Agent SDK query failed"

    return result


async def run_agent_loop_streaming(
    user_prompt: str,
    options: ClaudeAgentOptions,
    on_event: Callable[[TraceEvent], Awaitable[None]] | None = None,
) -> AgentLoopResult:
    """Run the Agent SDK query loop with streaming event callbacks.

    Like run_agent_loop but emits TraceEvent via on_event as the agent works.
    When on_event is None, behaves identically to run_agent_loop.
    """
    result = AgentLoopResult()

    async def _emit(event: TraceEvent) -> None:
        if on_event is not None:
            try:
                await on_event(event)
            except Exception:
                log.warning("Trace event callback failed for %s event", event.kind, exc_info=True)

    try:
        async for message in query(prompt=user_prompt, options=options):
            if isinstance(message, AssistantMessage):
                result.turns_used += 1
                for block in message.content:
                    if isinstance(block, TextBlock):
                        result.text_parts.append(block.text)
                        await _emit(TraceEvent(kind="text", content=block.text))
                    elif isinstance(block, ToolUseBlock):
                        path = extract_file_path(block.name, block.input)
                        if path and path not in result.files_written:
                            result.files_written.append(path)
                        await _emit(TraceEvent(
                            kind="tool_start",
                            tool_name=block.name,
                            tool_input=block.input,
                        ))
            elif isinstance(message, UserMessage):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        content = block.content if isinstance(block.content, str) else str(block.content)
                        await _emit(TraceEvent(kind="tool_result", content=content))
            elif isinstance(message, ResultMessage):
                turns = getattr(message, "num_turns", result.turns_used)
                cost = getattr(message, "total_cost_usd", None)
                await _emit(TraceEvent(
                    kind="complete",
                    turns_used=turns,
                    cost_usd=cost,
                    files_written=list(result.files_written),
                ))
    except Exception:
        log.exception("Agent SDK query failed")
        result.error = "Agent SDK query failed"
        await _emit(TraceEvent(kind="error", content="Agent SDK query failed"))

    return result
