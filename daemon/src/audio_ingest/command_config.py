"""Daemon-specific command configuration for TelegramInterface."""
from telegram_interface import CommandConfig

from .prompts import NOTE_SYSTEM_PROMPT, TASK_SYSTEM_PROMPT, ASK_SYSTEM_PROMPT, CHAT_SYSTEM_PROMPT
from .tools import TOOLS_ASK, TOOLS_TASK

DAEMON_COMMANDS = [
    CommandConfig(name="note", system_prompt=NOTE_SYSTEM_PROMPT, tools=TOOLS_TASK, description="Capture a fleeting note"),
    CommandConfig(name="ask", system_prompt=ASK_SYSTEM_PROMPT, tools=TOOLS_ASK, description="Query the PKM"),
    CommandConfig(name="task", system_prompt=TASK_SYSTEM_PROMPT, tools=TOOLS_TASK, description="Log a task"),
    CommandConfig(name="chat", system_prompt=CHAT_SYSTEM_PROMPT, tools=TOOLS_ASK, description="Free-form conversation"),
]
