"""Thin async client over the Plaud MCP server (stdio transport)."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable
from contextlib import AsyncExitStack
from datetime import date
from typing import Any, TypeVar

log = logging.getLogger(__name__)

_AUTH_RE = re.compile(
    r"(?i)\b(auth\w*|log(?:ged)?[ -]?in|unauthori[sz]ed|token|401"
    r"|session expired|invalid_grant|refresh failed)\b"
)
_WRAPPED_RE = re.compile(
    r"<untrusted-user-data-[^>]*>\s*(.*?)\s*</untrusted-user-data-[^>]*>", re.DOTALL
)
_PAGE_SIZE = 100
_TRANSCRIPT_LIMIT = 500
_CALL_TIMEOUT_S = 120.0
_MAX_PAGES = 1000

_T = TypeVar("_T")


class PlaudMcpError(Exception):
    """A Plaud MCP call failed."""


class PlaudAuthError(PlaudMcpError):
    """The Plaud MCP server is not authenticated."""


def parse_tool_text(text: str) -> dict:
    match = _WRAPPED_RE.search(text)
    body = match.group(1) if match else text.strip()
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise PlaudMcpError("unparseable Plaud MCP tool text") from exc
    if not isinstance(data, dict):
        raise PlaudMcpError("Plaud MCP tool text is not a JSON object")
    return data


def _joined_text(result: Any) -> str:
    parts = [
        item.text
        for item in (getattr(result, "content", None) or [])
        if isinstance(getattr(item, "text", None), str)
    ]
    return "\n".join(parts)


def _attr(result: Any, snake: str, camel: str) -> Any:
    value = getattr(result, snake, None)
    if value is None:
        value = getattr(result, camel, None)
    return value


def _list_field(data: dict, key: str, tool: str) -> list[dict]:
    items = data.get(key)
    if isinstance(items, list):
        return items
    payload = json.dumps(data, ensure_ascii=True)[:500]
    if _AUTH_RE.search(payload):
        raise PlaudAuthError(payload)
    raise PlaudMcpError(
        f"Plaud MCP {tool} response has no list under {key!r}: {payload}"
    )


async def _timed(name: str, awaitable: Awaitable[_T]) -> _T:
    try:
        async with asyncio.timeout(_CALL_TIMEOUT_S):
            return await awaitable
    except TimeoutError as exc:
        raise PlaudMcpError(
            f"Plaud MCP {name} timed out after {_CALL_TIMEOUT_S:g}s"
        ) from exc


class PlaudMcpClient:
    def __init__(self, package: str, command: str = "npx") -> None:
        self._package = package
        self._command = command
        self._stack: AsyncExitStack | None = None
        self._session: Any = None

    async def __aenter__(self) -> "PlaudMcpClient":
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=self._command, args=["-y", self._package])
        stack = AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await _timed("initialize", session.initialize())
        except BaseException as exc:
            try:
                await stack.aclose()
            except Exception:
                log.warning(
                    "Plaud MCP cleanup after failed start raised", exc_info=True
                )
            if isinstance(exc, PlaudMcpError) or not isinstance(exc, Exception):
                raise
            raise PlaudMcpError(f"failed to start Plaud MCP server: {exc}") from exc
        self._stack = stack
        self._session = session
        return self

    async def __aexit__(self, *exc: object) -> None:
        stack, self._stack = self._stack, None
        self._session = None
        if stack is not None:
            await stack.__aexit__(*exc)

    @classmethod
    def from_session(cls, session: Any) -> "PlaudMcpClient":
        client = cls.__new__(cls)
        client._package = ""
        client._command = ""
        client._stack = None
        client._session = session
        return client

    async def _call(self, name: str, arguments: dict[str, Any]) -> dict:
        if self._session is None:
            raise PlaudMcpError("Plaud MCP client is not started")
        try:
            result = await _timed(name, self._session.call_tool(name, arguments))
        except PlaudMcpError:
            raise
        except Exception as exc:
            raise PlaudMcpError(f"Plaud MCP call {name} failed: {exc}") from exc
        text = _joined_text(result)
        if _attr(result, "is_error", "isError"):
            if _AUTH_RE.search(text):
                raise PlaudAuthError(text)
            raise PlaudMcpError(text)
        structured = _attr(result, "structured_content", "structuredContent")
        if isinstance(structured, dict):
            return structured
        return parse_tool_text(text)

    async def list_files(self, date_from: date, date_to: date) -> list[dict]:
        files: list[dict] = []
        for page in range(1, _MAX_PAGES + 1):
            data = await self._call(
                "list_files",
                {
                    "date_from": date_from.isoformat(),
                    "date_to": date_to.isoformat(),
                    "page": page,
                    "page_size": _PAGE_SIZE,
                },
            )
            items = _list_field(data, "data", "list_files")
            files.extend(items)
            if len(items) < _PAGE_SIZE:
                return files
        raise PlaudMcpError(f"Plaud MCP list_files exceeded {_MAX_PAGES} pages")

    async def get_file(self, file_id: str) -> dict:
        return await self._call("get_file", {"file_id": file_id})

    async def _paged(self, file_id: str, block: str, key: str) -> list[dict]:
        out: list[dict] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(_MAX_PAGES):
            args: dict[str, Any] = {
                "file_id": file_id,
                "block": block,
                "limit": _TRANSCRIPT_LIMIT,
            }
            if cursor:
                args["cursor"] = cursor
            data = await self._call("get_transcript", args)
            out.extend(_list_field(data, key, f"get_transcript({block})"))
            cursor = data.get("next_cursor")
            if not cursor:
                return out
            if cursor in seen:
                raise PlaudMcpError(
                    f"Plaud MCP get_transcript({block}) repeated cursor {cursor!r}"
                )
            seen.add(cursor)
        raise PlaudMcpError(
            f"Plaud MCP get_transcript({block}) exceeded {_MAX_PAGES} pages"
        )

    async def get_transcript(self, file_id: str) -> list[dict]:
        return await self._paged(file_id, "transaction", "segments")

    async def get_marks(self, file_id: str) -> list[dict]:
        return await self._paged(file_id, "mark_memo", "marks")
