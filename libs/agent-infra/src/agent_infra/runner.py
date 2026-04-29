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
from .utils import extract_aux_path, extract_file_path

log = logging.getLogger(__name__)

_TOOL_ERRORS_CAP = 4


def _record_tool_error(result: AgentLoopResult, content: str) -> None:
    """Track an SDK tool-call rejection or MCP error on the result.

    Truncates the content to 500 chars and bounds the list to the last
    _TOOL_ERRORS_CAP entries so a runaway agent looping on the same rejection
    doesn't grow memory unboundedly.
    """
    snippet = content[:500] if content else ""
    log.warning("Agent tool call returned error: %s", snippet)
    result.tool_errors.append(snippet)
    if len(result.tool_errors) > _TOOL_ERRORS_CAP:
        del result.tool_errors[0 : len(result.tool_errors) - _TOOL_ERRORS_CAP]


def _record_tool_use(result: AgentLoopResult, tool_name: str, tool_input: dict) -> None:
    """Classify a tool-use call and track its target path on the result.

    Primary writes (vault_write/append/edit) populate `files_written`; aux
    mutations (vault_update_frontmatter/vault_add_links) populate
    `frontmatter_updated`/`links_added` so dedup-skip decisions are auditable.
    """
    path = extract_file_path(tool_name, tool_input)
    if path and path not in result.files_written:
        result.files_written.append(path)
        return
    aux = extract_aux_path(tool_name, tool_input)
    if aux is None:
        return
    kind, aux_path = aux
    target = result.frontmatter_updated if kind == "frontmatter" else result.links_added
    if aux_path not in target:
        target.append(aux_path)


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
                        _record_tool_use(result, block.name, block.input)
            elif isinstance(message, UserMessage):
                for block in message.content:
                    if isinstance(block, ToolResultBlock) and getattr(block, "is_error", False):
                        content = block.content if isinstance(block.content, str) else str(block.content)
                        _record_tool_error(result, content)
            elif isinstance(message, ResultMessage):
                turns = getattr(message, "num_turns", result.turns_used)
                result.turns_used = turns
                if getattr(message, "stop_reason", None) == "max_turns":
                    result.error = f"Agent hit turn limit ({turns} turns)"
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
                        _record_tool_use(result, block.name, block.input)
                        await _emit(TraceEvent(
                            kind="tool_start",
                            tool_name=block.name,
                            tool_input=block.input,
                        ))
            elif isinstance(message, UserMessage):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        content = block.content if isinstance(block.content, str) else str(block.content)
                        if getattr(block, "is_error", False):
                            _record_tool_error(result, content)
                        await _emit(TraceEvent(kind="tool_result", content=content))
            elif isinstance(message, ResultMessage):
                turns = getattr(message, "num_turns", result.turns_used)
                result.turns_used = turns
                cost = getattr(message, "total_cost_usd", None)
                if getattr(message, "stop_reason", None) == "max_turns":
                    result.error = f"Agent hit turn limit ({turns} turns)"
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
