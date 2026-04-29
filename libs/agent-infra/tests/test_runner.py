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
    async def test_max_turns_stop_sets_error(self, tmp_path) -> None:
        msg = make_assistant_message(text_blocks=["partial work"])
        result_msg = make_result_message(num_turns=40, stop_reason="max_turns")

        async def mock_query(prompt, options):
            yield msg
            yield result_msg

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop("prompt", opts)

        assert result.error == "Agent hit turn limit (40 turns)"
        assert result.text_parts == ["partial work"]

    @pytest.mark.asyncio
    async def test_end_turn_stop_does_not_set_error(self, tmp_path) -> None:
        msg = make_assistant_message(text_blocks=["done"])
        result_msg = make_result_message(num_turns=5, stop_reason="end_turn")

        async def mock_query(prompt, options):
            yield msg
            yield result_msg

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop("prompt", opts)

        assert result.error is None

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
    async def test_max_turns_stop_sets_error(self, tmp_path) -> None:
        msg = make_assistant_message(text_blocks=["partial work"])
        result_msg = make_result_message(num_turns=40, stop_reason="max_turns")

        async def mock_query(prompt, options):
            yield msg
            yield result_msg

        events: list[TraceEvent] = []

        async def collect(event: TraceEvent) -> None:
            events.append(event)

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop_streaming("prompt", opts, on_event=collect)

        assert result.error == "Agent hit turn limit (40 turns)"
        assert result.text_parts == ["partial work"]
        complete = next(e for e in events if e.kind == "complete")
        assert complete.turns_used == 40

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


class TestToolErrorCollection:
    """Tool-call rejections / MCP errors are collected on AgentLoopResult.tool_errors.

    Without this, an agent that gives up after a rejection looks identical to
    "agent did nothing" -- see PR #2 review (silent-failure-hunter C1).
    """

    @pytest.mark.asyncio
    async def test_streaming_records_is_error_tool_results(self, tmp_path) -> None:
        assistant_msg = make_assistant_message(
            tool_blocks=[("mcp__obsidian-pkm__vault_write", {"path": "x.md"})],
        )
        user_msg = make_user_message(tool_results=[
            ("tool-1", "Tool 'vault_write' is not in allowed_tools list", True),
        ])
        result_msg = make_result_message(num_turns=1)

        async def mock_query(prompt, options):
            yield assistant_msg
            yield user_msg
            yield result_msg

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop_streaming("prompt", opts)

        assert result.tool_errors == ["Tool 'vault_write' is not in allowed_tools list"]
        # error stays None -- the runner only collects, the consumer interprets
        assert result.error is None

    @pytest.mark.asyncio
    async def test_streaming_ignores_successful_tool_results(self, tmp_path) -> None:
        assistant_msg = make_assistant_message(
            tool_blocks=[("mcp__obsidian-pkm__vault_read", {"path": "x.md"})],
        )
        user_msg = make_user_message(tool_results=[("tool-1", "file contents", False)])
        result_msg = make_result_message(num_turns=1)

        async def mock_query(prompt, options):
            yield assistant_msg
            yield user_msg
            yield result_msg

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop_streaming("prompt", opts)

        assert result.tool_errors == []

    @pytest.mark.asyncio
    async def test_non_streaming_records_is_error_tool_results(self, tmp_path) -> None:
        assistant_msg = make_assistant_message(
            tool_blocks=[("mcp__obsidian-pkm__vault_trash", {"path": "x.md"})],
        )
        user_msg = make_user_message(tool_results=[
            ("tool-1", "Tool not allowed", True),
        ])
        result_msg = make_result_message(num_turns=1)

        async def mock_query(prompt, options):
            yield assistant_msg
            yield user_msg
            yield result_msg

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop(
                "prompt", opts,
            )

        assert result.tool_errors == ["Tool not allowed"]

    @pytest.mark.asyncio
    async def test_tool_errors_capped_at_4(self, tmp_path) -> None:
        """Bound the list so a runaway agent looping on the same rejection
        can't unbounded-grow memory."""
        assistant_msg = make_assistant_message(
            tool_blocks=[("mcp__obsidian-pkm__vault_write", {"path": "x.md"})],
        )
        # 6 errors -> only last 4 kept
        user_msg = make_user_message(tool_results=[
            (f"tool-{i}", f"err-{i}", True) for i in range(6)
        ])
        result_msg = make_result_message(num_turns=1)

        async def mock_query(prompt, options):
            yield assistant_msg
            yield user_msg
            yield result_msg

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop_streaming("prompt", opts)

        assert result.tool_errors == ["err-2", "err-3", "err-4", "err-5"]

    @pytest.mark.asyncio
    async def test_streaming_assigns_canonical_turns_used_from_result_message(self, tmp_path) -> None:
        """Streaming runner must persist ResultMessage.num_turns into result.turns_used.

        Pre-fix bug: streaming only read num_turns into a local for the max_turns
        error message and the 'complete' trace event; result.turns_used kept the
        message-counter value, which can drift from the SDK's authoritative count.
        """
        assistant_msg = make_assistant_message(text_blocks=["a"])
        result_msg = make_result_message(num_turns=42)

        async def mock_query(prompt, options):
            yield assistant_msg
            yield result_msg

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop_streaming("prompt", opts)

        # Message counter saw 1 AssistantMessage; SDK reported 42; canonical wins.
        assert result.turns_used == 42


class TestAuxPathTracking:
    """vault_update_frontmatter and vault_add_links populate separate fields.

    Without separate tracking, the per-item write protocol's dedup-hit branch
    (similarity > 0.8 -> append/edit/update_frontmatter on the existing note +
    bidirectional link insertion) is invisible to the daemon: a successful
    routing that touched 5 notes via aux mutations would look identical to
    "agent only wrote the inbox summary." See PR #2 review C3.
    """

    @pytest.mark.asyncio
    async def test_streaming_records_frontmatter_update(self, tmp_path) -> None:
        msg = make_assistant_message(tool_blocks=[
            ("mcp__obsidian-pkm__vault_write", {"path": "00-Inbox/note.md"}),
            ("mcp__obsidian-pkm__vault_update_frontmatter",
             {"path": "01-Projects/X/_index.md", "fields": {"updated": "2026-04-29"}}),
        ])

        async def mock_query(prompt, options):
            yield msg

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop_streaming("prompt", opts)

        assert result.files_written == ["00-Inbox/note.md"]
        assert result.frontmatter_updated == ["01-Projects/X/_index.md"]
        assert result.links_added == []

    @pytest.mark.asyncio
    async def test_streaming_records_link_additions(self, tmp_path) -> None:
        msg = make_assistant_message(tool_blocks=[
            ("mcp__obsidian-pkm__vault_write", {"path": "00-Inbox/note.md"}),
            ("mcp__obsidian-pkm__vault_add_links",
             {"path": "01-Projects/X/_index.md", "links": ["[[00-Inbox/note]]"]}),
        ])

        async def mock_query(prompt, options):
            yield msg

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop_streaming("prompt", opts)

        assert result.files_written == ["00-Inbox/note.md"]
        assert result.frontmatter_updated == []
        assert result.links_added == ["01-Projects/X/_index.md"]

    @pytest.mark.asyncio
    async def test_aux_paths_are_deduplicated(self, tmp_path) -> None:
        """If the agent calls vault_add_links twice on the same note, the path
        appears once. Same dedup invariant as files_written."""
        msg = make_assistant_message(tool_blocks=[
            ("mcp__obsidian-pkm__vault_add_links", {"path": "01-Projects/X/_index.md"}),
            ("mcp__obsidian-pkm__vault_add_links", {"path": "01-Projects/X/_index.md"}),
        ])

        async def mock_query(prompt, options):
            yield msg

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop_streaming("prompt", opts)

        assert result.links_added == ["01-Projects/X/_index.md"]

    @pytest.mark.asyncio
    async def test_non_streaming_records_aux_paths(self, tmp_path) -> None:
        msg = make_assistant_message(tool_blocks=[
            ("mcp__obsidian-pkm__vault_update_frontmatter",
             {"path": "01-Projects/X/_index.md", "fields": {}}),
            ("mcp__obsidian-pkm__vault_add_links",
             {"path": "00-Inbox/note.md", "links": []}),
        ])

        async def mock_query(prompt, options):
            yield msg

        with _patch_runner(mock_query):
            opts = build_agent_options("sys", tmp_path)
            result = await run_agent_loop("prompt", opts)

        assert result.frontmatter_updated == ["01-Projects/X/_index.md"]
        assert result.links_added == ["00-Inbox/note.md"]
