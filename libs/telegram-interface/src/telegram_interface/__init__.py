"""Telegram bot with agent-powered commands and trace streaming."""
from .types import BotConfig, CommandConfig, StatusSection, StatusProvider
from .bot import (
    TELEGRAM_API,
    send_message, send_message_return_id, edit_message_text,
    create_forum_topic, close_forum_topic, reopen_forum_topic,
    check_topics_enabled, poll_telegram_updates,
    to_telegram_markdown, markdown_to_telegram_html, escape_html,
)
from .commands import TelegramInterface, parse_command
from .thread_store import ThreadStore, ThreadRecord
from .trace import TelegramStreamSender, format_trace_event, tool_category

__all__ = [
    "BotConfig", "CommandConfig", "StatusSection", "StatusProvider",
    "TELEGRAM_API",
    "send_message", "send_message_return_id", "edit_message_text",
    "create_forum_topic", "close_forum_topic", "reopen_forum_topic",
    "check_topics_enabled", "poll_telegram_updates",
    "to_telegram_markdown", "markdown_to_telegram_html", "escape_html",
    "TelegramInterface", "parse_command",
    "ThreadStore", "ThreadRecord",
    "TelegramStreamSender", "format_trace_event", "tool_category",
]
