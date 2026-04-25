"""Trace rendering for streaming agent events.

TraceFormatter: pure formatting (TraceEvent -> string, no I/O).
TelegramStreamSender: rate-limited Telegram message editing with overflow.
"""
from __future__ import annotations

import logging
import time

import httpx

from agent_infra import TraceEvent
from .types import BotConfig
from .bot import edit_message_text, escape_html, markdown_to_telegram_html, send_message_return_id

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TraceFormatter — pure formatting, no I/O
# ---------------------------------------------------------------------------

_TOOL_DISPLAY: dict[str, tuple[str, str]] = {
    "vault_read": ("\U0001f4d6", "Reading"),
    "vault_peek": ("\U0001f4d6", "Peeking"),
    "vault_write": ("\u270f\ufe0f", "Writing"),
    "vault_append": ("\u270f\ufe0f", "Appending"),
    "vault_edit": ("\u270f\ufe0f", "Editing"),
    "vault_search": ("\U0001f50d", "Searching"),
    "vault_semantic_search": ("\U0001f50d", "Semantic search"),
    "vault_list": ("\U0001f4c2", "Listing"),
}

_QUERY_TOOLS = {"vault_search", "vault_semantic_search"}

# Write tools and which input key holds the content being written
_WRITE_CONTENT_KEY: dict[str, str] = {
    "vault_write": "content",
    "vault_append": "content",
    "vault_edit": "new_string",
}

_MAX_WRITE_PREVIEW = 400

# Native Claude Code tools: (emoji, label, input_key_to_display)
_NATIVE_TOOL_FORMAT: dict[str, tuple[str, str, str]] = {
    "Read": ("\U0001f4c4", "Read", "file_path"),
    "Write": ("\U0001f4dd", "Write", "file_path"),
    "Edit": ("\u270f\ufe0f", "Edit", "file_path"),
    "Bash": ("\U0001f4bb", "Bash", "command"),
    "Task": ("\U0001f500", "Task", "description"),
    "Glob": ("\U0001f50d", "Glob", "pattern"),
    "Grep": ("\U0001f50d", "Grep", "pattern"),
}

_MAX_NATIVE_PARAM = 80

# Category grouping for logical message breaks (keyed by short tool name)
_TOOL_CATEGORY: dict[str, str] = {
    "vault_read": "read", "vault_peek": "read",
    "vault_write": "write", "vault_append": "write", "vault_edit": "write",
    "vault_search": "search", "vault_semantic_search": "search",
    "vault_list": "browse", "vault_recent": "browse",
    "vault_links": "browse", "vault_neighborhood": "browse",
    "vault_query": "search", "vault_tags": "browse", "vault_activity": "browse",
    "Read": "read", "Write": "write", "Edit": "write",
    "Bash": "execute", "Task": "execute",
    "Glob": "search", "Grep": "search",
}


def tool_category(tool_name: str) -> str:
    """Return the logical category for a tool name."""
    short = _strip_mcp_prefix(tool_name)
    return _TOOL_CATEGORY.get(short, "other")


def _strip_mcp_prefix(tool_name: str) -> str:
    prefix = "mcp__obsidian-pkm__"
    if tool_name.startswith(prefix):
        return tool_name[len(prefix):]
    return tool_name


def _strip_frontmatter(text: str) -> str:
    """Strip YAML frontmatter (---...---) from note content."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:].lstrip("\n")
    return text


def _format_write_preview(tool_input: dict, short_name: str) -> str:
    """Format a truncated, indented preview of write content.

    Content is converted to Telegram HTML: HTML-escaped first (safe for <, >, &),
    then **bold** -> <b>bold</b> and ## headings -> <b>heading</b>.
    Conversion happens before indentation so heading regex matches at line start.
    """
    content_key = _WRITE_CONTENT_KEY.get(short_name, "")
    content = tool_input.get(content_key, "")
    if not content:
        return ""
    content = _strip_frontmatter(content)
    if not content.strip():
        return ""
    if len(content) > _MAX_WRITE_PREVIEW:
        content = content[:_MAX_WRITE_PREVIEW].rstrip() + "\n..."
    content = markdown_to_telegram_html(content)
    lines = content.rstrip("\n").split("\n")
    indented = "\n".join(f"   {line}" for line in lines)
    return indented + "\n"


def _format_tool_start(event: TraceEvent) -> str:
    raw_name = event.tool_name or "unknown"
    short_name = _strip_mcp_prefix(raw_name)
    display = _TOOL_DISPLAY.get(short_name)
    tool_input = event.tool_input or {}

    if display:
        emoji, label = display
        param_key = "query" if short_name in _QUERY_TOOLS else "path"
        param_value = escape_html(tool_input.get(param_key, ""))
        header = f"{emoji} {label}: {param_value}\n" if param_value else f"{emoji} {label}\n"
    elif raw_name in _NATIVE_TOOL_FORMAT:
        emoji, label, input_key = _NATIVE_TOOL_FORMAT[raw_name]
        param_value = escape_html(str(tool_input.get(input_key, "")))
        if len(param_value) > _MAX_NATIVE_PARAM:
            param_value = param_value[:_MAX_NATIVE_PARAM] + "..."
        header = f"{emoji} {label}: {param_value}\n" if param_value else f"{emoji} {label}\n"
    else:
        friendly = short_name.replace("_", " ").title()
        header = f"\U0001f527 {friendly}\n"

    if short_name in _WRITE_CONTENT_KEY:
        header += _format_write_preview(tool_input, short_name)

    return header


_MAX_RESULT_CHARS = 2000


def _format_tool_result(event: TraceEvent) -> str:
    content = event.content
    if not content:
        return "   (empty)\n"
    if len(content) > _MAX_RESULT_CHARS:
        content = content[:_MAX_RESULT_CHARS] + "\n... (truncated)"
    lines = content.split("\n")
    indented = "\n".join(f"   {line}" for line in lines)
    return indented + "\n"


def _format_text(event: TraceEvent) -> str:
    content = event.content or ""
    return f"\n\U0001f4ad {content}\n"


def _format_complete(event: TraceEvent) -> str:
    parts = ["\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"]
    stats = []
    if event.turns_used is not None:
        stats.append(f"{event.turns_used} turns")
    if event.cost_usd is not None:
        stats.append(f"${event.cost_usd:.2f}")
    if stats:
        parts.append(" \u00b7 ".join(stats) + "\n")
    return "".join(parts)


def _format_error(event: TraceEvent) -> str:
    return f"\n\u274c {escape_html(event.content or 'Unknown error')}\n"


def format_trace_event(event: TraceEvent) -> str:
    """Format a TraceEvent into a display string. Pure function, no I/O."""
    formatters = {
        "tool_start": _format_tool_start,
        "tool_result": _format_tool_result,
        "text": _format_text,
        "complete": _format_complete,
        "error": _format_error,
    }
    formatter = formatters.get(event.kind)
    if formatter is None:
        log.warning("Unknown trace event kind %r, formatting as error", event.kind)
        formatter = _format_error
    return formatter(event)


# ---------------------------------------------------------------------------
# TelegramStreamSender — rate-limited Telegram message editing with overflow
# ---------------------------------------------------------------------------

_OVERFLOW_THRESHOLD = 3800
_DEFAULT_FLUSH_INTERVAL = 1.0


class TelegramStreamSender:
    """Rate-limited Telegram message sender for streaming agent traces.

    Accumulates formatted trace lines, sends new messages at logical breaks
    (tool category changes, text events, completion), and handles overflow.
    """

    def __init__(
        self,
        tg: BotConfig,
        thread_id: int | None = None,
        flush_interval: float = _DEFAULT_FLUSH_INTERVAL,
    ):
        self._tg = tg
        self._thread_id = thread_id
        self._flush_interval = flush_interval
        self._buffer: list[str] = []
        self._sent_text = ""
        self._current_msg_id: int | None = None
        self._last_flush_time: float = 0.0
        self._message_ids: list[int] = []
        self._last_tool_category: str | None = None

    def _start_new_message(self) -> None:
        """Mark that the next flush should create a new message."""
        self._current_msg_id = None
        self._sent_text = ""

    async def handle(self, event: TraceEvent) -> None:
        """Receive a TraceEvent, format it, and buffer for sending."""
        # Skip tool results and text — tool_start shows tools used,
        # and text output is already in the final response message.
        if event.kind in ("tool_result", "text"):
            return

        # Complete: append turn count footer, flush, and reset
        if event.kind == "complete":
            if event.turns_used is not None:
                self._buffer.append(f"\n────────────────\n{event.turns_used} turns\n")
            await self.flush()
            self._start_new_message()
            self._last_tool_category = None
            return

        # Determine if we need a logical break (new message)
        if event.kind == "tool_start":
            cat = tool_category(event.tool_name or "")
            if self._last_tool_category is not None and cat != self._last_tool_category:
                await self.flush()
                self._start_new_message()
            self._last_tool_category = cat
        elif event.kind == "error":
            if self._buffer or self._sent_text:
                await self.flush()
                self._start_new_message()
            self._last_tool_category = None

        formatted = format_trace_event(event)
        self._buffer.append(formatted)

        if event.kind == "error":
            await self.flush()
        elif time.monotonic() - self._last_flush_time >= self._flush_interval:
            await self.flush()

    async def flush(self) -> None:
        """Send buffered content to Telegram."""
        if not self._buffer:
            return

        new_text = "".join(self._buffer)
        self._buffer.clear()
        full_text = self._sent_text + new_text

        if len(full_text) > _OVERFLOW_THRESHOLD and self._current_msg_id is not None:
            self._current_msg_id = None
            self._sent_text = ""
            full_text = new_text

        if self._current_msg_id is None:
            try:
                msg_id = await self._send_html(full_text)
                if msg_id:
                    self._current_msg_id = msg_id
                    self._message_ids.append(msg_id)
                    self._sent_text = full_text
                else:
                    log.warning("send_message_return_id returned None; %d chars not delivered", len(full_text))
            except Exception:
                log.exception("Failed to send trace message")
        else:
            try:
                await edit_message_text(
                    self._tg.chat_id, self._current_msg_id, full_text, self._tg,
                    thread_id=self._thread_id, parse_mode="HTML",
                )
                self._sent_text = full_text
            except Exception:
                log.warning("Failed to edit trace message, sending new")
                self._current_msg_id = None
                self._sent_text = ""
                try:
                    msg_id = await self._send_html(full_text)
                    if msg_id:
                        self._current_msg_id = msg_id
                        self._message_ids.append(msg_id)
                        self._sent_text = full_text
                except Exception:
                    log.exception("Failed to send fallback trace message")

        self._last_flush_time = time.monotonic()

    async def _send_html(self, text: str) -> int | None:
        """Send with parse_mode='HTML', falling back to plain text on 400."""
        try:
            return await send_message_return_id(
                text, self._tg, thread_id=self._thread_id, parse_mode="HTML",
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400:
                log.warning("HTML send failed (400), retrying as plain text")
                return await send_message_return_id(
                    text, self._tg, thread_id=self._thread_id,
                )
            raise
