from __future__ import annotations

import json as json_mod
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

from telegram_interface import BotConfig, TELEGRAM_API, send_message, send_message_return_id

if TYPE_CHECKING:
    from .extraction import AgentRoutingResult

log = logging.getLogger(__name__)


def obsidian_link(vault_path: str, vault_name: str = "PKM") -> str:
    """Convert a vault-relative path to a clickable Obsidian URI Markdown link.

    E.g. "01-Projects/Automation/tasks/foo.md"
    -> "[foo](obsidian://open?vault=PKM&file=01-Projects%2FAutomation%2Ftasks%2Ffoo.md)"
    """
    # Strip .md extension for the display name
    display = Path(vault_path).stem
    encoded = quote(vault_path, safe="")
    uri = f"obsidian://open?vault={quote(vault_name, safe='')}&file={encoded}"
    return f"[{display}]({uri})"


def format_file_list(files: list[str], vault_name: str = "PKM") -> str:
    """Format a list of vault-relative paths as clickable Obsidian links."""
    return "\n".join(f"  {obsidian_link(f, vault_name)}" for f in files)


async def send_routing_summary(
    routing_result: "AgentRoutingResult | None", tg: BotConfig,
    *, thread_id: int | None = None,
) -> None:
    """Notify about agent routing results. Accepts AgentRoutingResult or None."""
    if routing_result is None:
        text = (
            "\U0001f399 New recording processed\n\n"
            "Agent routing failed. Raw transcript preserved."
        )
        await send_message(text, tg, thread_id=thread_id)
        return

    if not routing_result.success:
        text = (
            "\U0001f399 New recording processed\n\n"
            f"Agent routing failed: {routing_result.error or 'unknown error'}\n"
            "Raw transcript preserved."
        )
        await send_message(text, tg, thread_id=thread_id)
        return

    files = routing_result.files_written
    file_lines = "\n".join(f"  * {obsidian_link(f)}" for f in files) if files else "  (none)"

    # Truncate summary for Telegram
    summary = routing_result.summary
    summary_display = summary[:500] + "..." if len(summary) > 500 else summary

    # Dedup-skip footer: only render if the agent did meaningful aux work, so
    # the typical case (no dedup hits) doesn't add visual noise. Empty footer
    # = empty extra newlines path.
    aux_count = (
        len(routing_result.frontmatter_updated) + len(routing_result.links_added)
    )
    aux_footer = (
        f"\n\nDedup-hit updates: "
        f"{len(routing_result.frontmatter_updated)} frontmatter, "
        f"{len(routing_result.links_added)} backlinks."
        if aux_count else ""
    )

    text = (
        f"\U0001f399 New recording processed\n\n"
        f"{summary_display}\n\n"
        f"Files written ({len(files)}):\n{file_lines}"
        f"{aux_footer}\n\n"
        f"Agent used {routing_result.turns_used} turn(s)."
    )

    await send_message(text, tg, thread_id=thread_id)


async def send_error(job_id: str, error: str, tg: BotConfig,
                     *, thread_id: int | None = None) -> None:
    text = (
        f"\u26a0\ufe0f Audio processing failed\n\n"
        f"Job: {job_id}\n"
        f"Error: {error}\n"
        f"Audio preserved in cloud relay for retry."
    )
    await send_message(text, tg, thread_id=thread_id)


async def send_transcription_error(
    recording_name: str,
    error: str,
    tg: BotConfig,
    *,
    thread_id: int | None = None,
) -> None:
    """Notify user about a transcription pipeline failure."""
    # Truncate error to avoid hitting Telegram message limits
    error_display = error[:300] + "..." if len(error) > 300 else error
    text = (
        "\u26a0\ufe0f Transcription Pipeline Failed\n\n"
        f"Recording: {recording_name}\n"
        f"Error: {error_display}\n\n"
        "Audio file preserved for retry."
    )
    await send_message(text, tg, thread_id=thread_id)


async def answer_callback_query(
    callback_query_id: str, text: str | None, tg: BotConfig,
) -> None:
    """Acknowledge a callback query (removes loading spinner in Telegram)."""
    url = f"{TELEGRAM_API}/bot{tg.bot_token}/answerCallbackQuery"
    payload: dict = {"callback_query_id": callback_query_id}
    if text is not None:
        payload["text"] = text
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()


async def edit_message_reply_markup(
    chat_id: str, message_id: int, reply_markup: dict | None, tg: BotConfig,
) -> None:
    """Edit or remove the reply markup (inline keyboard) on a message.

    Telegram's Bot API expects `reply_markup` as a JSON-serialised string
    even though we're posting JSON — serialising it here keeps callers
    passing native dicts and matches send_message_return_id's behaviour.
    """
    url = f"{TELEGRAM_API}/bot{tg.bot_token}/editMessageReplyMarkup"
    payload: dict = {"chat_id": chat_id, "message_id": message_id}
    if reply_markup is not None:
        payload["reply_markup"] = json_mod.dumps(reply_markup)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
