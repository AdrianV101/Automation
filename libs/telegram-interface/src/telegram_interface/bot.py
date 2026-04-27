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
    max_concurrent_dispatch: int = 4,
) -> None:
    """Long-running coroutine. Uses Telegram long-polling (timeout=30).

    Dispatches handlers concurrently up to max_concurrent_dispatch at a time, so
    one slow handler does not block subsequent updates.

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

    sem = asyncio.Semaphore(max_concurrent_dispatch)
    active: set[asyncio.Task] = set()

    async def _run_handler(
        name: str,
        coro: Awaitable[None],
        *,
        error_text: str = "Failed to process command. Check daemon logs.",
        thread_id: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        async with sem:
            try:
                await coro
            except Exception:
                log.exception("Error in %s handler", name)
                if client is not None:
                    try:
                        await _send_message_with_client(
                            error_text,
                            tg, client, thread_id=thread_id,
                        )
                    except Exception:
                        log.warning(
                            "Failed to notify user of %s handler error",
                            name, exc_info=True,
                        )

    def _spawn(name: str, coro: Awaitable[None], **err_kwargs) -> None:
        t = asyncio.create_task(_run_handler(name, coro, **err_kwargs))
        active.add(t)
        t.add_done_callback(active.discard)

    async with httpx.AsyncClient(timeout=45.0) as client:
        try:
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

                        cbq = update.get("callback_query")
                        if cbq and on_callback_query:
                            cbq_msg = cbq.get("message", {})
                            cbq_chat_id = str(cbq_msg.get("chat", {}).get("id", ""))
                            if cbq_chat_id == tg.chat_id:
                                cbq_id = cbq["id"]
                                cbq_message_id = cbq_msg.get("message_id")
                                cbq_data = cbq.get("data", "")
                                if cbq_message_id and cbq_data:
                                    _spawn(
                                        "callback_query",
                                        on_callback_query(cbq_id, cbq_message_id, cbq_data),
                                    )
                            continue

                        message = update.get("message", {})
                        text = message.get("text", "").strip()
                        if not text:
                            continue
                        chat_id = str(message.get("chat", {}).get("id", ""))
                        if chat_id != tg.chat_id:
                            continue

                        reply_to = message.get("reply_to_message")
                        thread_id = message.get("message_thread_id")

                        if thread_id and on_topic_message is not None:
                            _spawn(
                                "topic_message",
                                on_topic_message(thread_id, text),
                                error_text="Failed to process message. Check daemon logs.",
                                thread_id=thread_id, client=client,
                            )
                        elif reply_to and on_labeling_reply is not None:
                            reply_to_id = reply_to.get("message_id")
                            if reply_to_id is not None:
                                _spawn(
                                    "labeling_reply",
                                    on_labeling_reply(reply_to_id, text),
                                    error_text="Failed to process labeling reply. Check daemon logs.",
                                    client=client,
                                )
                        elif on_message is not None:
                            _spawn(
                                "message", on_message(text),
                                client=client,
                            )

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
        finally:
            # Cancel and drain in-flight handlers while the httpx client is
            # still open — handlers in their except branch may send error
            # notifications via the client.
            # Yield once first so freshly-spawned tasks can reach their first
            # await before we cancel (needed for graceful shutdown).
            if active:
                await asyncio.sleep(0)
            for t in list(active):
                t.cancel()
            for t in list(active):
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                except Exception:
                    log.warning(
                        "In-flight Telegram handler raised during shutdown drain",
                        exc_info=True,
                    )
