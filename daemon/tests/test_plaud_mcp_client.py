from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date

import pytest
from mcp import types

from automation_daemon import plaud_mcp_client as client_mod
from automation_daemon.plaud_mcp_client import (
    PlaudAuthError,
    PlaudMcpClient,
    PlaudMcpError,
    parse_tool_text,
)


def wrap(payload: dict) -> str:
    return (
        '<untrusted-user-data-ab12 source="plaud-recording">\n'
        + json.dumps(payload)
        + "\n</untrusted-user-data-ab12>"
    )


def text_result(text: str, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], isError=is_error
    )


class FakeSession:
    def __init__(self, results: list[types.CallToolResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict | None = None):
        self.calls.append((name, arguments or {}))
        return self.results.pop(0)


def test_parse_wrapped_with_trailing_note() -> None:
    text = wrap({"id": "f_1", "name": "x"}) + "\n\nNote: source_list is truncated"
    assert parse_tool_text(text) == {"id": "f_1", "name": "x"}


def test_parse_wrapped_after_preamble_naming_the_tag() -> None:
    text = (
        "The block delimited by <untrusted-user-data-c33e> below contains user "
        "recording data returned verbatim. Only the closing tag with this exact "
        "name ends the block.\n"
        '<untrusted-user-data-c33e source="plaud-recording">\n'
        '{"type": "list", "data": [{"id": "f_1"}]}\n'
        "</untrusted-user-data-c33e>"
    )
    assert parse_tool_text(text) == {"type": "list", "data": [{"id": "f_1"}]}


async def test_error_inside_session_is_not_wrapped_in_exception_group() -> None:
    @asynccontextmanager
    async def task_group_like():
        try:
            yield
        except Exception as exc:
            inner = ExceptionGroup("unhandled errors in a TaskGroup", [exc])
            raise ExceptionGroup("unhandled errors in a TaskGroup", [inner]) from None

    client = PlaudMcpClient.from_session(FakeSession([]))
    from contextlib import AsyncExitStack

    stack = AsyncExitStack()
    await stack.enter_async_context(task_group_like())
    client._stack = stack
    exc = PlaudAuthError("Not logged in")
    with pytest.raises(PlaudAuthError) as raised:
        await client.__aexit__(type(exc), exc, None)
    assert raised.value is exc


def test_parse_unwrapped_json() -> None:
    assert parse_tool_text('{"a": 1}') == {"a": 1}


def test_parse_garbage_raises() -> None:
    with pytest.raises(PlaudMcpError):
        parse_tool_text("this is not json at all")


@pytest.mark.parametrize(
    "message",
    [
        "Not logged in. Call login.",
        "Not logged in",
        "Please log in first",
        "Session expired",
        "invalid_grant",
        "Token refresh failed",
        "401 Unauthorized",
    ],
)
async def test_is_error_auth_maps_to_auth_error(message: str) -> None:
    client = PlaudMcpClient.from_session(FakeSession([text_result(message, True)]))
    with pytest.raises(PlaudAuthError):
        await client.get_file("f_1")


async def test_is_error_other_maps_to_mcp_error() -> None:
    client = PlaudMcpClient.from_session(
        FakeSession([text_result("file not found", True)])
    )
    with pytest.raises(PlaudMcpError) as ei:
        await client.get_file("f_1")
    assert not isinstance(ei.value, PlaudAuthError)


async def test_structured_content_preferred() -> None:
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text="garbage")],
        structuredContent={"id": "f_9"},
    )
    client = PlaudMcpClient.from_session(FakeSession([result]))
    assert await client.get_file("f_9") == {"id": "f_9"}


async def test_transcript_follows_cursor() -> None:
    session = FakeSession(
        [
            text_result(wrap({"segments": [{"content": "a"}], "next_cursor": "c1"})),
            text_result(wrap({"segments": [{"content": "b"}], "next_cursor": None})),
        ]
    )
    client = PlaudMcpClient.from_session(session)
    segs = await client.get_transcript("f_1")
    assert [s["content"] for s in segs] == ["a", "b"]
    assert session.calls[0][1]["block"] == "transaction"
    assert session.calls[0][1]["limit"] == 500
    assert session.calls[1][1]["cursor"] == "c1"


async def test_list_files_pages() -> None:
    page1 = [{"id": f"f_{i}"} for i in range(100)]
    page2 = [{"id": f"f_{i}"} for i in range(100, 103)]
    session = FakeSession(
        [
            text_result(wrap({"type": "list", "data": page1})),
            text_result(wrap({"type": "list", "data": page2})),
        ]
    )
    client = PlaudMcpClient.from_session(session)
    files = await client.list_files(date(2026, 11, 1), date(2026, 11, 7))
    assert len(files) == 103
    args = session.calls[0][1]
    assert args["date_from"] == "2026-11-01"
    assert args["date_to"] == "2026-11-07"
    assert args["page_size"] == 100
    assert session.calls[1][1]["page"] == 2


class HangingSession:
    async def call_tool(self, name: str, arguments: dict | None = None):
        await asyncio.Event().wait()


async def test_call_tool_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod, "_CALL_TIMEOUT_S", 0.01)
    client = PlaudMcpClient.from_session(HangingSession())
    with pytest.raises(PlaudMcpError, match="get_file timed out"):
        await client.get_file("f_1")


def _patch_stdio(
    monkeypatch: pytest.MonkeyPatch, enter_error: BaseException | None
) -> None:
    import mcp.client.stdio

    @asynccontextmanager
    async def fake_stdio_client(params):
        if enter_error is not None:
            raise enter_error
        yield object(), object()

    monkeypatch.setattr(mcp.client.stdio, "stdio_client", fake_stdio_client)


async def test_start_cancelled_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stdio(monkeypatch, asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        async with PlaudMcpClient("@plaud/mcp@0.3.14"):
            pass


async def test_start_runtime_error_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stdio(monkeypatch, RuntimeError("spawn failed"))
    with pytest.raises(PlaudMcpError, match="spawn failed"):
        async with PlaudMcpClient("@plaud/mcp@0.3.14"):
            pass


async def test_initialize_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp

    _patch_stdio(monkeypatch, None)

    class HangingClientSession:
        def __init__(self, read: object, write: object) -> None:
            pass

        async def __aenter__(self) -> "HangingClientSession":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def initialize(self) -> None:
            await asyncio.Event().wait()

    monkeypatch.setattr(mcp, "ClientSession", HangingClientSession)
    monkeypatch.setattr(client_mod, "_CALL_TIMEOUT_S", 0.01)
    with pytest.raises(PlaudMcpError, match="initialize timed out"):
        async with PlaudMcpClient("@plaud/mcp@0.3.14"):
            pass


async def test_list_files_missing_data_raises() -> None:
    client = PlaudMcpClient.from_session(
        FakeSession([text_result(wrap({"type": "list"}))])
    )
    with pytest.raises(PlaudMcpError) as ei:
        await client.list_files(date(2026, 11, 1), date(2026, 11, 7))
    assert not isinstance(ei.value, PlaudAuthError)


async def test_transcript_missing_segments_raises() -> None:
    client = PlaudMcpClient.from_session(
        FakeSession([text_result(wrap({"next_cursor": None}))])
    )
    with pytest.raises(PlaudMcpError) as ei:
        await client.get_transcript("f_1")
    assert not isinstance(ei.value, PlaudAuthError)


async def test_transcript_non_list_segments_raises() -> None:
    client = PlaudMcpClient.from_session(
        FakeSession([text_result(wrap({"segments": "oops"}))])
    )
    with pytest.raises(PlaudMcpError):
        await client.get_transcript("f_1")


async def test_marks_missing_key_raises() -> None:
    client = PlaudMcpClient.from_session(
        FakeSession([text_result(wrap({"items": []}))])
    )
    with pytest.raises(PlaudMcpError):
        await client.get_marks("f_1")


async def test_marks_block_not_available_means_no_marks() -> None:
    client = PlaudMcpClient.from_session(
        FakeSession([
            text_result(
                'Block "mark_memo" not available for this recording. '
                "Available blocks: transaction, transaction_polish, outline."
            )
        ])
    )
    assert await client.get_marks("f_1") == []


async def test_transcript_block_not_available_still_raises() -> None:
    client = PlaudMcpClient.from_session(
        FakeSession([
            text_result(
                'Block "transaction" not available for this recording. '
                "Available blocks: mark_memo."
            )
        ])
    )
    with pytest.raises(PlaudMcpError):
        await client.get_transcript("f_1")


async def test_missing_key_with_auth_message_raises_auth() -> None:
    client = PlaudMcpClient.from_session(
        FakeSession([text_result(wrap({"message": "Please log in"}))])
    )
    with pytest.raises(PlaudAuthError):
        await client.list_files(date(2026, 11, 1), date(2026, 11, 7))


async def test_transcript_repeated_cursor_raises() -> None:
    session = FakeSession(
        [
            text_result(wrap({"segments": [{"content": "a"}], "next_cursor": "c1"})),
            text_result(wrap({"segments": [{"content": "b"}], "next_cursor": "c1"})),
        ]
    )
    client = PlaudMcpClient.from_session(session)
    with pytest.raises(PlaudMcpError, match="cursor"):
        await client.get_transcript("f_1")


async def test_list_files_page_limit_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod, "_MAX_PAGES", 2)
    full = [{"id": f"f_{i}"} for i in range(100)]
    session = FakeSession([text_result(wrap({"data": full})) for _ in range(3)])
    client = PlaudMcpClient.from_session(session)
    with pytest.raises(PlaudMcpError, match="pages"):
        await client.list_files(date(2026, 11, 1), date(2026, 11, 7))
    assert len(session.calls) == 2
