"""Daemon-specific command configuration for TelegramInterface."""
from __future__ import annotations

from pathlib import Path

from telegram_interface import CommandConfig

from .prompts import (
    build_ask_system_prompt,
    build_chat_system_prompt,
    build_note_system_prompt,
    build_task_system_prompt,
)
from .tools import TOOLS_ASK, TOOLS_TASK


def build_daemon_commands(vault_path: Path) -> list[CommandConfig]:
    return [
        CommandConfig(name="note", system_prompt=build_note_system_prompt(vault_path), tools=TOOLS_TASK, description="Capture a fleeting note"),
        CommandConfig(name="ask", system_prompt=build_ask_system_prompt(vault_path), tools=TOOLS_ASK, description="Query the PKM"),
        CommandConfig(name="task", system_prompt=build_task_system_prompt(vault_path), tools=TOOLS_TASK, description="Log a task"),
        CommandConfig(name="chat", system_prompt=build_chat_system_prompt(vault_path), tools=TOOLS_ASK, description="Free-form conversation"),
    ]
