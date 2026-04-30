from __future__ import annotations

import asyncio
import hashlib
import logging
import re
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


_NOTES_HEADING_RE = re.compile(
    r"^##\s+Notes\s*$", re.MULTILINE,
)


def _extract_notes_section(master_text: str) -> str:
    """Return the substring from '## Notes' to EOF, or '' if no such heading.

    Whitespace around the heading is preserved verbatim — the SHA must change
    if a single byte changes inside (or just before) the section.
    """
    match = _NOTES_HEADING_RE.search(master_text)
    if not match:
        return ""
    return master_text[match.start():]


def _hash_notes_section(master_path: Path) -> str | None:
    """SHA256 of the '## Notes' section of the master doc, or None if no doc."""
    if not master_path.is_file():
        return None
    body = master_path.read_text()
    section = _extract_notes_section(body)
    return hashlib.sha256(section.encode("utf-8")).hexdigest()


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

    # Snapshot Notes section BEFORE the agent run so we can detect clobbering.
    master_path = _master_doc_path(config.vault_root, target_date)
    notes_hash_before = _hash_notes_section(master_path)

    last_error: str | None = None
    backoffs = config.retry_backoff_seconds or (0.0,)
    for attempt, wait_s in enumerate(backoffs, start=1):
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        try:
            output = await run_agent(AgentRunInput(
                target_date=target_date,
                vault_root=config.vault_root,
                model=config.model,
            ))
        except Exception as exc:
            log.exception(
                "Agent run raised on attempt %d/%d for %s",
                attempt, len(backoffs), target_date,
            )
            last_error = f"agent_exception: {exc}"
            continue

        if not output.success:
            last_error = output.error or "agent_returned_unsuccessful"
            continue

        # Verify the Notes section if it existed before.
        if notes_hash_before is not None:
            notes_hash_after = _hash_notes_section(master_path)
            if notes_hash_after != notes_hash_before:
                await db.update_run(
                    target_date, status="failed_notes_clobbered",
                    error="agent modified ## Notes section",
                )
                await notify(
                    f"⚠️ News master doc for {target_date.isoformat()} "
                    "rejected: agent modified the ## Notes section. "
                    "Master doc may need manual revert.",
                )
                return

        await db.update_run(
            target_date, status="completed",
            master_path=str(master_path.relative_to(config.vault_root)),
            item_count=output.item_count,
        )
        await notify(
            f"📰 News daily master ready — {target_date.isoformat()} "
            f"({output.item_count} items across "
            f"{len(output.categories)} categories)"
            + (
                f"\nNew categories: {', '.join(output.new_categories)}"
                if output.new_categories else ""
            ),
        )
        return

    # All attempts exhausted.
    await db.update_run(
        target_date, status="failed", error=last_error or "unknown",
    )
    await notify(
        f"⚠️ News daily master failed for {target_date.isoformat()}: {last_error}",
    )
