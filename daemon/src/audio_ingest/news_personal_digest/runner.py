from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from agent_infra import build_agent_options, run_agent_loop_streaming

from .prompt import build_runner_prompt, render_feedback_block
from .render import DigestCategory, DigestItem, build_messages
from .state import DigestItemInput, NewsDigestStateDB

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DigestRunnerConfig:
    vault_root: Path
    model: str = "claude-opus-4-7"
    feedback_window_days: int = 7
    min_items: int = 4
    max_items: int = 12
    telegram_topic_id: int | None = None


@dataclass(frozen=True)
class AgentRunInput:
    target_date: date
    vault_root: Path
    model: str
    prompt: str


@dataclass
class AgentRunOutput:
    """Structured summary returned by the digest agent.

    Same success/error pair invariant as news_daily_master's AgentRunOutput:
    success implies error is None; failure requires a non-None error.
    """
    success: bool
    categories: list[DigestCategory] = field(default_factory=list)
    rating_signal_summary: str = ""
    text: str = ""
    error: str | None = None

    def __post_init__(self) -> None:
        if self.success and self.error is not None:
            raise ValueError(
                "AgentRunOutput(success=True) must have error=None; "
                f"got error={self.error!r}",
            )
        if not self.success and self.error is None:
            raise ValueError(
                "AgentRunOutput(success=False) requires a non-None error",
            )

    @property
    def item_count(self) -> int:
        return sum(len(c.items) for c in self.categories)


AgentRunFn = Callable[[AgentRunInput], Awaitable[AgentRunOutput]]
NotifyFn = Callable[[str], Awaitable[None]]
SendMessagesFn = Callable[
    [list[tuple[str, dict]]], Awaitable[list[int]],
]


def _master_doc_path(vault_root: Path, target_date: date) -> Path:
    return (
        vault_root / "01-Projects" / "News" / "daily"
        / f"{target_date.isoformat()}-master.md"
    )


async def run_for_date(
    target_date: date,
    *,
    db: NewsDigestStateDB,
    config: DigestRunnerConfig,
    run_agent: AgentRunFn,
    notify: NotifyFn,
    send_messages: SendMessagesFn,
) -> None:
    """Execute one digest generation for `target_date`.

    Pure orchestration: state transitions + agent invocation + Telegram
    delivery. Concrete agent + Telegram callables are injected so tests
    can substitute fakes.
    """
    await db.insert_run(target_date)
    try:
        await _run_for_date_inner(
            target_date,
            db=db, config=config,
            run_agent=run_agent, notify=notify, send_messages=send_messages,
        )
    except Exception as exc:
        log.exception("Unexpected error in digest run_for_date for %s", target_date)
        try:
            await db.update_run(
                target_date, status="failed",
                error=f"unexpected: {exc}",
            )
        except Exception:
            log.exception("Could not mark digest %s failed", target_date)
        try:
            await notify(
                f"⚠️ News personal digest crashed for {target_date.isoformat()}: "
                f"{exc}",
            )
        except Exception:
            log.exception("Could not notify after crash for %s", target_date)


async def _run_for_date_inner(
    target_date: date,
    *,
    db: NewsDigestStateDB,
    config: DigestRunnerConfig,
    run_agent: AgentRunFn,
    notify: NotifyFn,
    send_messages: SendMessagesFn,
) -> None:
    master_path = _master_doc_path(config.vault_root, target_date)
    if not master_path.is_file():
        log.info("No master doc for %s — skipping digest", target_date)
        await db.update_run(target_date, status="skipped_no_master")
        return

    # Inner branches (happy / failed / failed_verification) added in Task I.
    raise NotImplementedError("happy/error paths added in subsequent tasks")
