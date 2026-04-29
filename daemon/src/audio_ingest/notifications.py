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


def _build_inline_keyboard(
    cluster_id: str, known_names: list[str] | None,
) -> dict:
    """Build an InlineKeyboardMarkup for speaker labeling."""
    rows: list[list[dict]] = []

    if known_names:
        # Name buttons in rows of 2
        for i in range(0, len(known_names), 2):
            row = []
            for name in known_names[i:i + 2]:
                row.append({
                    "text": name,
                    "callback_data": f"{cluster_id}:{name}",
                })
            rows.append(row)

    # Always add Other + Ignore as last row
    rows.append([
        {"text": "Other...", "callback_data": f"{cluster_id}:__other__"},
        {"text": "Ignore", "callback_data": f"{cluster_id}:__ignore__"},
    ])

    return {"inline_keyboard": rows}


async def send_speaker_labeling_prompt(
    cluster_id: str,
    recording_name: str,
    sample_text: str,
    tg: BotConfig,
    *,
    voice_clip_path: Path | None = None,
    known_names: list[str] | None = None,
    thread_id: int | None = None,
) -> int | None:
    """Send labeling prompt with inline keyboard. Returns message_id.

    If voice_clip_path is provided, sends a voice message with caption.
    Otherwise sends a text message with inline keyboard.
    """
    text = (
        "\U0001f50a Unknown Speaker Detected\n\n"
        f"Recording: {recording_name}\n"
        f"Cluster: {cluster_id}\n\n"
        f"Sample of what they said:\n"
        f"\"{sample_text}\"\n\n"
        "Tap a name below, or tap \"Other...\" to type a name."
    )
    keyboard = _build_inline_keyboard(cluster_id, known_names)

    async with httpx.AsyncClient(timeout=15.0) as client:
        if voice_clip_path and voice_clip_path.exists():
            # sendVoice with multipart form data
            url = f"{TELEGRAM_API}/bot{tg.bot_token}/sendVoice"
            data: dict = {
                "chat_id": tg.chat_id,
                "caption": text,
                "reply_markup": json_mod.dumps(keyboard),
            }
            if thread_id is not None:
                data["message_thread_id"] = str(thread_id)
            with open(voice_clip_path, "rb") as f:
                resp = await client.post(url, data=data,
                    files={"voice": (voice_clip_path.name, f, "audio/ogg")})
        else:
            # sendMessage with inline keyboard
            url = f"{TELEGRAM_API}/bot{tg.bot_token}/sendMessage"
            payload: dict = {
                "chat_id": tg.chat_id,
                "text": text,
                "reply_markup": keyboard,
            }
            if thread_id is not None:
                payload["message_thread_id"] = thread_id
            resp = await client.post(url, json=payload)

        resp.raise_for_status()
        data = resp.json()
        return data.get("result", {}).get("message_id")


async def send_speaker_labeled_confirmation(
    name: str, tg: BotConfig,
) -> int | None:
    """Confirm that a speaker profile was created/updated. Returns message_id."""
    text = f"\u2705 Speaker profile created for \"{name}\""
    url = f"{TELEGRAM_API}/bot{tg.bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json={
            "chat_id": tg.chat_id,
            "text": text,
        })
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", {}).get("message_id")


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
    """Edit or remove the reply markup (inline keyboard) on a message."""
    url = f"{TELEGRAM_API}/bot{tg.bot_token}/editMessageReplyMarkup"
    payload: dict = {"chat_id": chat_id, "message_id": message_id}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
