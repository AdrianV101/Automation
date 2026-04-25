"""Tests for agent_infra.runner — run_agent_loop and run_agent_loop_streaming."""
from __future__ import annotations

import contextlib
from unittest.mock import patch

import pytest

from agent_infra import build_agent_options, run_agent_loop, run_agent_loop_streaming, TraceEvent
from tests.conftest import (
    MockAssistantMessage,
    MockResultMessage,
    MockTextBlock,
    MockToolResultBlock,
    MockToolUseBlock,
    MockUserMessage,
    make_assistant_message,
    make_result_message,
    make_user_message,
)


def _patch_runner(mock_query_fn):
    """Return an ExitStack-compatible context manager that patches runner module."""
    patches = [
        patch("agent_infra.runner.query", side_effect=mock_query_fn),
        patch("agent_infra.runner.AssistantMessage", MockAssistantMessage),
        patch("agent_infra.runner.TextBlock", MockTextBlock),
        patch("agent_infra.runner.ToolUseBlock", MockToolUseBlock),
        patch("agent_infra.runner.UserMessage", MockUserMessage),
        patch("agent_infra.runner.ToolResultBlock", MockToolResultBlock),
        patch("agent_infra.runner.ResultMessage", MockResultMessage),
    ]
    stack = contextlib.ExitStack()
    for p in patches:
        stack.enter_context(p)
    return stack


class TestRunAgentLoop:
    """Tests for run_agent_loop."""

    @pytest.mark.asyncio
    async def test_collects_text_and_files(self, tmp_path) -> None:
        msg = make_assistant_message(
            text_blocks=["Summary text."],
            tool_blocks=[("mcp__obsidian-pkm__vault_write", {"path": "00-Inbox/note.md"})],
        )

        async def mock_query(prompt, options):
            yield msg

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path, allowed_tools=["mcp__obsidian-pkm__vault_write"])
            result = await run_agent_loop("user prompt", opts)

        assert result.text_parts == ["Summary text."]
        assert result.files_written == ["00-Inbox/note.md"]
        assert result.turns_used == 1
        assert result.error is None

    @pytest.mark.asyncio
    async def test_deduplicates_files(self, tmp_path) -> None:
        msg1 = make_assistant_message(
            tool_blocks=[("mcp__obsidian-pkm__vault_write", {"path": "00-Inbox/note.md"})],
        )
        msg2 = make_assistant_message(
            tool_blocks=[("mcp__obsidian-pkm__vault_append", {"path": "00-Inbox/note.md"})],
        )

        async def mock_query(prompt, options):
            yield msg1
            yield msg2

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop("prompt", opts)

        assert result.files_written == ["00-Inbox/note.md"]
        assert result.turns_used == 2

    @pytest.mark.asyncio
    async def test_exception_sets_error(self, tmp_path) -> None:
        async def mock_query(prompt, options):
            raise RuntimeError("boom")
            yield  # noqa: RET503 — make this an async generator

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop("prompt", opts)

        assert result.error == "Agent SDK query failed"
        assert result.text_parts == []

    @pytest.mark.asyncio
    async def test_empty_response(self, tmp_path) -> None:
        async def mock_query(prompt, options):
            return
            yield  # noqa: RET503 — make this an async generator

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop("prompt", opts)

        assert result.text_parts == []
        assert result.files_written == []
        assert result.turns_used == 0
        assert result.error is None


class TestRunAgentLoopStreaming:
    """Tests for run_agent_loop_streaming."""

    @pytest.mark.asyncio
    async def test_emits_text_and_tool_events(self, tmp_path) -> None:
        assistant_msg = make_assistant_message(
            text_blocks=["Hello world."],
            tool_blocks=[("mcp__obsidian-pkm__vault_write", {"path": "note.md"})],
        )
        user_msg = make_user_message(tool_results=[("tool-1", "ok")])
        result_msg = make_result_message(num_turns=1, total_cost_usd=0.03)

        async def mock_query(prompt, options):
            yield assistant_msg
            yield user_msg
            yield result_msg

        events: list[TraceEvent] = []

        async def collect(event: TraceEvent) -> None:
            events.append(event)

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop_streaming("prompt", opts, on_event=collect)

        # Verify result
        assert result.text_parts == ["Hello world."]
        assert result.files_written == ["note.md"]
        assert result.turns_used == 1
        assert result.error is None

        # Verify events
        kinds = [e.kind for e in events]
        assert "text" in kinds
        assert "tool_start" in kinds
        assert "tool_result" in kinds
        assert "complete" in kinds

        text_event = next(e for e in events if e.kind == "text")
        assert text_event.content == "Hello world."

        tool_start = next(e for e in events if e.kind == "tool_start")
        assert tool_start.tool_name == "mcp__obsidian-pkm__vault_write"

        tool_result = next(e for e in events if e.kind == "tool_result")
        assert tool_result.content == "ok"

        complete = next(e for e in events if e.kind == "complete")
        assert complete.turns_used == 1
        assert complete.cost_usd == 0.03
        assert complete.files_written == ["note.md"]

    @pytest.mark.asyncio
    async def test_returns_agent_loop_result(self, tmp_path) -> None:
        msg = make_assistant_message(text_blocks=["output"])

        async def mock_query(prompt, options):
            yield msg

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop_streaming("prompt", opts)

        from agent_infra import AgentLoopResult
        assert isinstance(result, AgentLoopResult)
        assert result.text_parts == ["output"]

    @pytest.mark.asyncio
    async def test_no_callback_works(self, tmp_path) -> None:
        """run_agent_loop_streaming with on_event=None should not raise."""
        msg = make_assistant_message(text_blocks=["text"])

        async def mock_query(prompt, options):
            yield msg

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop_streaming("prompt", opts, on_event=None)

        assert result.text_parts == ["text"]
        assert result.error is None

    @pytest.mark.asyncio
    async def test_error_emits_error_event(self, tmp_path) -> None:
        async def mock_query(prompt, options):
            raise RuntimeError("agent failed")
            yield  # noqa: RET503

        events: list[TraceEvent] = []

        async def collect(event: TraceEvent) -> None:
            events.append(event)

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop_streaming("prompt", opts, on_event=collect)

        assert result.error == "Agent SDK query failed"
        assert len(events) == 1
        assert events[0].kind == "error"
        assert events[0].content == "Agent SDK query failed"

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_crash(self, tmp_path) -> None:
        """If on_event raises, the loop continues."""
        msg = make_assistant_message(text_blocks=["text1", "text2"])

        async def mock_query(prompt, options):
            yield msg

        call_count = 0

        async def flaky_callback(event: TraceEvent) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("callback exploded")

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop_streaming("prompt", opts, on_event=flaky_callback)

        # Both text blocks should still be collected even though callback failed on first
        assert result.text_parts == ["text1", "text2"]
        assert result.error is None
