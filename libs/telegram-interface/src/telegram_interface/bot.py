from __future__ import annotations

import asyncio
import html
import logging
import re
from collections.abc import Awaitable, Callable

import httpx

from .types import BotConfig

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


def escape_html(text: str) -> str:
    """Escape HTML special characters (<, >, &) for Telegram HTML parse mode."""
    return html.escape(text, quote=False)


def markdown_to_telegram_html(text: str) -> str:
    """Convert standard Markdown to Telegram HTML format.

    HTML-escapes all content first (safe for <, >, &), then converts
    **bold** -> <b>bold</b> and ## headings -> <b>heading</b>.
    """
    text = escape_html(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    return text


def to_telegram_markdown(text: str) -> str:
    """Convert standard Markdown to Telegram legacy Markdown format.

    Telegram's Markdown mode uses *bold* (single asterisk) while standard
    Markdown uses **bold** (double asterisk). This converts the most common
    patterns so messages render correctly.
    """
    # Convert **bold** to *bold* (Telegram bold)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    # Convert ## Heading to *Heading* (Telegram has no heading support)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    return text


async def check_topics_enabled(tg: BotConfig) -> bool:
    """Check if forum topics are enabled for the bot's chat."""
    url = f"{TELEGRAM_API}/bot{tg.bot_token}/getChat"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={"chat_id": tg.chat_id})
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", {}).get("is_forum", False)
    except Exception:
        log.exception("Failed to check if topics are enabled")
        return False


async def send_message_return_id(
    text: str, tg: BotConfig, *, thread_id: int | None = None,
    parse_mode: str | None = None,
) -> int | None:
    """Send a message and return its message_id (for later editing)."""
    url = f"{TELEGRAM_API}/bot{tg.bot_token}/sendMessage"
    payload: dict = {"chat_id": tg.chat_id, "text": text}
    if thread_id is not None:
        payload["message_thread_id"] = thread_id
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json().get("result", {}).get("message_id")


async def send_message(
    text: str, tg: BotConfig, *, thread_id: int | None = None,
    parse_mode: str | None = None,
) -> None:
    await send_message_return_id(text, tg, thread_id=thread_id, parse_mode=parse_mode)


async def edit_message_text(
    chat_id: str, message_id: int, text: str, tg: BotConfig,
    *, thread_id: int | None = None, parse_mode: str | None = None,
) -> None:
    """Edit the text of an existing message."""
    url = f"{TELEGRAM_API}/bot{tg.bot_token}/editMessageText"
    payload: dict = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if thread_id is not None:
        payload["message_thread_id"] = thread_id
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()


async def _send_message_with_client(
    text: str, tg: BotConfig, client: httpx.AsyncClient,
    *, thread_id: int | None = None, parse_mode: str | None = None,
) -> None:
    url = f"{TELEGRAM_API}/bot{tg.bot_token}/sendMessage"
    payload: dict = {"chat_id": tg.chat_id, "text": text}
    if thread_id is not None:
        payload["message_thread_id"] = thread_id
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    resp = await client.post(url, json=payload)
    resp.raise_for_status()


async def create_forum_topic(name: str, tg: BotConfig) -> int | None:
    """Create a forum topic in the bot's private chat. Returns message_thread_id or None."""
    url = f"{TELEGRAM_API}/bot{tg.bot_token}/createForumTopic"
    payload = {"chat_id": tg.chat_id, "name": name[:128]}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", {}).get("message_thread_id")
    except Exception:
        log.exception("Failed to create forum topic %r", name)
        return None


async def close_forum_topic(thread_id: int, tg: BotConfig) -> None:
    """Close a forum topic."""
    url = f"{TELEGRAM_API}/bot{tg.bot_token}/closeForumTopic"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={
                "chat_id": tg.chat_id,
                "message_thread_id": thread_id,
            })
            resp.raise_for_status()
    except Exception:
        log.exception("Failed to close forum topic %d", thread_id)


async def reopen_forum_topic(thread_id: int, tg: BotConfig) -> bool:
    """Reopen a closed forum topic. Returns True if successful or already open."""
    url = f"{TELEGRAM_API}/bot{tg.bot_token}/reopenForumTopic"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={
                "chat_id": tg.chat_id,
                "message_thread_id": thread_id,
            })
            resp.raise_for_status()
            return True
    except httpx.HTTPStatusError as e:
        # 400 "TOPIC_NOT_MODIFIED" means it's already open -- that's fine
        if e.response.status_code == 400:
            try:
                body = e.response.json()
                desc = body.get("description", "")
                if "TOPIC_NOT_MODIFIED" in desc:
                    return True
            except Exception:
                pass
        log.warning("Failed to reopen forum topic %d: %s", thread_id, e)
        return False
    except Exception:
        log.exception("Failed to reopen forum topic %d", thread_id)
        return False


async def poll_telegram_updates(
    tg: BotConfig,
    on_labeling_reply: Callable[[int, str], Awaitable[None]] | None = None,
    *,
    on_callback_query: Callable[[str, int, str], Awaitable[None]] | None = None,
    on_message: Callable[[str], Awaitable[None]] | None = None,
    on_topic_message: Callable[[int, str], Awaitable[None]] | None = None,
) -> None:
    """Long-running coroutine. Uses Telegram long-polling (timeout=30).

    Calls on_labeling_reply(reply_to_message_id, text) for replies
    to bot messages (if provided).
    Calls on_callback_query(callback_query_id, message_id, data) for
    inline keyboard button presses.
    Calls on_topic_message(thread_id, text) for messages inside a
    named topic (forum thread).
    Calls on_message(text) for standalone (non-reply, non-topic) messages.
    """
    url = f"{TELEGRAM_API}/bot{tg.bot_token}/getUpdates"
    offset = 0
    backoff = 5

    allowed = ["message"]
    if on_callback_query is not None:
        allowed.append("callback_query")

    async with httpx.AsyncClient(timeout=45.0) as client:
        while True:
            try:
                params: dict = {"timeout": 30, "allowed_updates": allowed}
                if offset:
                    params["offset"] = offset

                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

                for update in data.get("result", []):
                    offset = update["update_id"] + 1

                    # Handle callback queries (inline keyboard button presses)
                    cbq = update.get("callback_query")
                    if cbq and on_callback_query:
                        cbq_msg = cbq.get("message", {})
                        cbq_chat_id = str(cbq_msg.get("chat", {}).get("id", ""))
                        if cbq_chat_id == tg.chat_id:
                            cbq_id = cbq["id"]
                            cbq_message_id = cbq_msg.get("message_id")
                            cbq_data = cbq.get("data", "")
                            if cbq_message_id and cbq_data:
                                try:
                                    await on_callback_query(cbq_id, cbq_message_id, cbq_data)
                                except Exception:
                                    log.exception(
                                        "Error handling callback query %s",
                                        cbq_id,
                                    )
                        continue

                    # Handle messages
                    message = update.get("message", {})
                    text = message.get("text", "").strip()

                    if not text:
                        continue

                    # Only process messages from the configured chat
                    chat_id = str(message.get("chat", {}).get("id", ""))
                    if chat_id != tg.chat_id:
                        continue

                    reply_to = message.get("reply_to_message")
                    thread_id = message.get("message_thread_id")
                    if thread_id and on_topic_message is not None:
                        # Message inside a named topic -> follow-up (check BEFORE reply_to
                        # because Telegram sets implicit reply_to on all topic messages)
                        try:
                            await on_topic_message(thread_id, text)
                        except Exception:
                            log.exception(
                                "Error handling topic message in thread %d",
                                thread_id,
                            )
                            try:
                                await _send_message_with_client(
                                    "Failed to process message. Check daemon logs.",
                                    tg, client, thread_id=thread_id,
                                )
                            except Exception:
                                log.debug("Failed to send error notification for topic message")
                    elif reply_to and on_labeling_reply is not None:
                        # Reply to a bot message in General -> labeling flow
                        reply_to_id = reply_to.get("message_id")
                        if reply_to_id is not None:
                            try:
                                await on_labeling_reply(reply_to_id, text)
                            except Exception:
                                log.exception(
                                    "Error handling labeling reply for message %d",
                                    reply_to_id,
                                )
                                try:
                                    await _send_message_with_client(
                                        f"Failed to process labeling reply for message {reply_to_id}. "
                                        "Check daemon logs.",
                                        tg, client,
                                    )
                                except Exception:
                                    log.debug("Failed to send error notification for labeling reply")
                    elif on_message is not None:
                        # Standalone message -> on_message callback
                        try:
                            await on_message(text)
                        except Exception:
                            log.exception(
                                "Error handling standalone message",
                            )
                            try:
                                await _send_message_with_client(
                                    "Failed to process command. Check daemon logs.",
                                    tg, client,
                                )
                            except Exception:
                                log.debug("Failed to send error notification for command")

                backoff = 5

            except httpx.TimeoutException:
                continue
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    log.critical("Telegram bot token rejected (HTTP %d), stopping poller", e.response.status_code)
                    return
                log.exception("Telegram polling HTTP error, retrying in %ds", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except Exception:
                log.exception("Telegram polling error, retrying in %ds", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
