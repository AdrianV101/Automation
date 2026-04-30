from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .state import NewsDailyMasterStateDB

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunnerConfig:
    vault_root: Path
    model: str = "claude-opus-4-7"
    telegram_topic_id: int | None = None
    retry_backoff_seconds: tuple[float, ...] = (0.0, 60.0, 300.0)
    mcp_server_path: str | None = None


@dataclass
class AgentRunInput:
    """Inputs to the agent runner callable; lets us inject a fake in tests."""
    target_date: date
    vault_root: Path
    model: str


@dataclass
class AgentRunOutput:
    """Structured summary returned by the agent runner."""
    success: bool
    item_count: int
    categories: list[str] = field(default_factory=list)
    new_categories: list[str] = field(default_factory=list)
    skipped_items: list[str] = field(default_factory=list)
    text: str = ""
    error: str | None = None


AgentRunFn = Callable[[AgentRunInput], Awaitable[AgentRunOutput]]
NotifyFn = Callable[[str], Awaitable[None]]


def _source_folder(vault_root: Path, target_date: date) -> Path:
    return vault_root / "00-Inbox" / "news" / target_date.isoformat()


def _master_doc_path(vault_root: Path, target_date: date) -> Path:
    return (
        vault_root / "01-Projects" / "News" / "daily"
        / f"{target_date.isoformat()}-master.md"
    )


def _has_source_items(vault_root: Path, target_date: date) -> bool:
    folder = _source_folder(vault_root, target_date)
    if not folder.is_dir():
        return False
    return any(folder.glob("*.md"))


async def run_for_date(
    target_date: date,
    *,
    db: NewsDailyMasterStateDB,
    config: RunnerConfig,
    run_agent: AgentRunFn,
    notify: NotifyFn,
) -> None:
    """Execute one master-doc generation for `target_date`.

    Pure orchestration: state transitions + agent invocation + notification.
    Does NOT itself talk to Claude or Telegram — those are passed as
    callables so tests can substitute fakes.
    """
    await db.insert_run(target_date)

    if not _has_source_items(config.vault_root, target_date):
        log.info("No news items for %s — skipping", target_date)
        await db.update_run(target_date, status="skipped_empty")
        return

    # Agent invocation + retry/notes-snapshot/verification logic comes in
    # subsequent tasks (H, I, J, K).
    raise NotImplementedError("happy path arrives in Task H")
