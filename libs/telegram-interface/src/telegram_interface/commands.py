"""Command dispatch and poller for Telegram bot interface."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import httpx
from agent_infra import SessionManager

from .bot import (
    create_forum_topic, poll_telegram_updates, send_message_return_id,
    edit_message_text, to_telegram_markdown,
)
from .trace import TelegramStreamSender
from .thread_store import ThreadStore
from .types import BotConfig, CommandConfig, StatusProvider

log = logging.getLogger(__name__)

HELP_PREFIX = "Available commands:\n\n"


def _topic_name(command: str, args: str) -> str:
    """Generate a topic name from command and args."""
    prefix = command.title()
    truncated = args[:30] + "..." if len(args) > 30 else args
    return f"{prefix}: {truncated}"


def parse_command(text: str, valid_commands: set[str]) -> tuple[str, str]:
    """Parse a command string into (command, args).

    Returns ("unknown", "") for unrecognized input.
    """
    text = text.strip()
    if not text.startswith("/"):
        return ("unknown", "")

    parts = text.split(None, 1)
    command = parts[0][1:].lower()

    if command not in valid_commands:
        return ("unknown", "")

    args = parts[1].strip() if len(parts) > 1 else ""

    # /status ignores arguments
    if command == "status":
        args = ""

    return (command, args)


async def _send_response(
    response_text: str, tg_text: str, bot: BotConfig,
    *, thread_id: int | None = None,
) -> None:
    """Send response as a new message, with Markdown fallback to plain text."""
    try:
        await send_message_return_id(tg_text, bot, thread_id=thread_id, parse_mode="Markdown")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 400:
            log.warning("Markdown send failed (400), retrying as plain text")
            await send_message_return_id(response_text, bot, thread_id=thread_id)
        else:
            raise


class TelegramInterface:
    """Configurable Telegram bot interface with command dispatch and session management."""

    def __init__(
        self,
        bot_config: BotConfig,
        commands: list[CommandConfig],
        session_manager: SessionManager,
        thread_store: ThreadStore,
        *,
        status_provider: StatusProvider | None = None,
        file_formatter: Callable[[list[str]], str] | None = None,
        agent_inactivity_timeout_s: float | None = None,
    ):
        self._bot = bot_config
        self._commands = {cmd.name: cmd for cmd in commands}
        self._valid_commands = {cmd.name for cmd in commands} | {"status"}
        self._session_mgr = session_manager
        self._thread_store = thread_store
        self._status_provider = status_provider
        self._file_formatter = file_formatter or _default_file_formatter
        self._agent_inactivity_timeout_s = agent_inactivity_timeout_s

    @property
    def help_text(self) -> str:
        lines = [HELP_PREFIX]
        for cmd in self._commands.values():
            desc = f" - {cmd.description}" if cmd.description else ""
            lines.append(f"/{cmd.name} <text>{desc}")
        lines.append("/status - System dashboard")
        return "\n".join(lines)

    async def dispatch_command(self, text: str) -> None:
        """Parse and dispatch a command."""
        command, args = parse_command(text, self._valid_commands)

        if command == "unknown":
            await send_message_return_id(self.help_text, self._bot)
            return

        if command == "status":
            dashboard = await self._build_status()
            await send_message_return_id(dashboard, self._bot)
            return

        cmd_config = self._commands.get(command)
        if not cmd_config:
            await send_message_return_id(self.help_text, self._bot)
            return

        if not args:
            await send_message_return_id(f"Usage: /{command} <text>", self._bot)
            return

        # Create topic
        thread_id: int | None = None
        try:
            thread_id = await create_forum_topic(_topic_name(command, args), self._bot)
        except Exception:
            log.exception("Failed to create forum topic for /%s", command)

        # Echo user message into topic so it's visible as context
        if thread_id is not None:
            try:
                await send_message_return_id(args, self._bot, thread_id=thread_id)
            except Exception:
                log.exception("Failed to echo user message for /%s", command)

        # Ack immediately (inside topic if created)
        ack_text = "Thinking..." if command in ("ask", "chat") else "Got it, routing..."
        try:
            await send_message_return_id(ack_text, self._bot, thread_id=thread_id)
        except Exception:
            log.exception("Failed to send ack for /%s", command)

        # Persist thread metadata so follow-ups can look up the command/prompt
        if thread_id is not None:
            topic_name = _topic_name(command, args)
            try:
                await self._thread_store.save_thread(
                    thread_id=thread_id, session_id="",
                    name=topic_name, command=command,
                )
            except Exception:
                log.exception("Failed to persist thread metadata for topic %d", thread_id)

        # Session key
        session_key = f"thread-{thread_id}" if thread_id else f"general-{command}"

        # Use session_manager.send() with streaming trace
        sender = TelegramStreamSender(self._bot, thread_id)

        try:
            resp = await self._session_mgr.send(
                session_key=session_key,
                message=args,
                system_prompt=cmd_config.system_prompt,
                allowed_tools=cmd_config.tools,
                on_event=sender.handle,
                inactivity_timeout_s=self._agent_inactivity_timeout_s,
            )
        except Exception:
            log.exception("Session send failed for /%s", command)
            try:
                await send_message_return_id(
                    "Failed to process command. Please try again.",
                    self._bot, thread_id=thread_id,
                )
            except Exception:
                log.warning("Failed to send error notification for /%s", command)
            return

        try:
            await sender.flush()
        except Exception:
            log.warning("Final trace flush failed", exc_info=True)

        # Format response
        response_text = self._format_response(resp)
        tg_text = to_telegram_markdown(response_text)
        await _send_response(response_text, tg_text, self._bot, thread_id=thread_id)

    def _format_response(self, resp) -> str:
        """Format a SessionResponse into display text."""
        if resp.error:
            response_text = f"Failed: {resp.error}"
        elif resp.files_written:
            files = self._file_formatter(resp.files_written)
            response_text = f"{resp.text}\n\nFiles:\n{files}"
        else:
            response_text = resp.text
        if len(response_text) > 4000:
            response_text = response_text[:3997] + "..."
        return response_text

    async def _build_status(self) -> str:
        """Build status dashboard from the StatusProvider."""
        if self._status_provider is None:
            return "No status providers configured."
        try:
            sections = await self._status_provider.get_status_sections()
            lines: list[str] = []
            for section in sections:
                lines.append(section.title + "\n")
                lines.extend(section.entries)
            return "\n".join(lines) or "No status data."
        except Exception:
            log.exception("Error building status")
            return "Error reading status data."

    async def _handle_topic_message(self, thread_id: int, text: str) -> None:
        """Handle follow-up messages inside topic threads."""
        record = await self._thread_store.get_thread(thread_id)
        if record is None:
            log.warning("Message in unknown topic %d, ignoring", thread_id)
            return

        cmd_config = self._commands.get(record.command)
        system_prompt = cmd_config.system_prompt if cmd_config else ""

        ack_id = None
        try:
            ack_id = await send_message_return_id("Thinking...", self._bot, thread_id=thread_id)
        except Exception:
            log.exception("Failed to send ack in topic %d", thread_id)

        sender = TelegramStreamSender(self._bot, thread_id=thread_id)

        try:
            result = await self._session_mgr.send(
                session_key=f"thread-{thread_id}",
                message=text,
                system_prompt=system_prompt,
                on_event=sender.handle,
                inactivity_timeout_s=self._agent_inactivity_timeout_s,
            )
        except Exception:
            log.exception("Session send failed in topic %d", thread_id)
            error_text = "Failed to process message. Please try again."
            try:
                if ack_id:
                    await edit_message_text(
                        self._bot.chat_id, ack_id, error_text, self._bot,
                        thread_id=thread_id,
                    )
                else:
                    await send_message_return_id(error_text, self._bot, thread_id=thread_id)
            except Exception:
                log.warning("Failed to send error notification in topic %d", thread_id)
            return

        try:
            await sender.flush()
        except Exception:
            log.warning("Final trace flush failed in topic %d", thread_id, exc_info=True)

        response_text = self._format_response(result)
        tg_text = to_telegram_markdown(response_text)

        try:
            if ack_id:
                try:
                    await edit_message_text(
                        self._bot.chat_id, ack_id, tg_text, self._bot,
                        thread_id=thread_id, parse_mode="Markdown",
                    )
                except Exception:
                    log.exception("Failed to edit ack in topic %d, sending new", thread_id)
                    await send_message_return_id(tg_text, self._bot, thread_id=thread_id, parse_mode="Markdown")
            else:
                await send_message_return_id(tg_text, self._bot, thread_id=thread_id, parse_mode="Markdown")
        except Exception:
            log.exception("Failed to send response in topic %d", thread_id)

    async def run_poller(
        self,
        on_labeling_reply: Callable[[int, str], Awaitable[None]] | None = None,
        on_callback_query: Callable[[str, int, str], Awaitable[None]] | None = None,
        max_concurrent_dispatch: int = 4,
    ) -> None:
        """Run Telegram polling loop. Dispatches commands and handles topic follow-ups."""
        await poll_telegram_updates(
            self._bot,
            on_labeling_reply,
            on_callback_query=on_callback_query,
            on_message=self.dispatch_command,
            on_topic_message=self._handle_topic_message,
            max_concurrent_dispatch=max_concurrent_dispatch,
        )


def _default_file_formatter(files: list[str]) -> str:
    """Format file paths for display, one per line with indentation."""
    return "\n".join(f"  {f}" for f in files)
