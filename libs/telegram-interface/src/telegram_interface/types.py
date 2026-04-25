from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class BotConfig:
    bot_token: str
    chat_id: str


@dataclass
class CommandConfig:
    name: str
    system_prompt: str
    tools: list[str]
    description: str = ""


@dataclass
class StatusSection:
    title: str
    entries: list[str] = field(default_factory=list)


@runtime_checkable
class StatusProvider(Protocol):
    async def get_status_sections(self) -> list[StatusSection]: ...
