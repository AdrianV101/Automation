from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from agent_infra import (
    AgentLoopResult,
    build_agent_options,
    run_agent_loop_streaming,
)
from agent_infra.runner import TraceEvent

from .prompt import (
    build_runner_prompt,
    render_ratings_block,
    render_recurrence_block,
)
from .recurrence import rank_recurring_threads
from .scheduler import iso_week_dates
from .state import NewsWeeklyStateDB

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunnerConfig:
    vault_root: Path
    model: str = "claude-sonnet-4-6"
    min_days: int = 3
    recurrence_threshold: int = 3
    max_threads: int = 8
    max_turns: int = 60
    feedback_window_days: int = 7
    # First entry is the wait before attempt 1 (always 0); rest gate retries.
    retry_backoff_seconds: tuple[float, ...] = (0.0, 60.0)


@dataclass
class AgentRunInput:
    iso_week: str
    vault_root: Path
    model: str
    max_threads: int
    max_turns: int
    prompt: str


@dataclass
class AgentRunOutput:
    """Structured summary from the weekly agent.

    Enforces the success/error pair invariant (mirrors news_research):
    success implies error is None; failure requires a non-None error.
    """
    success: bool
    threads_written: int = 0
    entities_enriched: int = 0
    cost_usd: float | None = None
    turns_used: int = 0
    text: str = ""
    error: str | None = None

    def __post_init__(self) -> None:
        if self.success and self.error is not None:
            raise ValueError(
                "AgentRunOutput(success=True) must have error=None; "
                f"got error={self.error!r}"
            )
        if not self.success and self.error is None:
            raise ValueError(
                "AgentRunOutput(success=False) requires a non-None error"
            )


AgentRunFn = Callable[[AgentRunInput], Awaitable[AgentRunOutput]]
RatingsFn = Callable[[date, date], Awaitable[list[dict[str, Any]]]]
NotifyFn = Callable[[str], Awaitable[None]]


def _weekly_note_path(vault_root: Path, iso_week: str) -> Path:
    return (
        vault_root / "01-Projects" / "News" / "weekly" / f"{iso_week}.md"
    )


def _master_path(vault_root: Path, d: date) -> Path:
    return (
        vault_root / "01-Projects" / "News" / "daily"
        / f"{d.isoformat()}-master.md"
    )


_NOTES_HEADING_RE = re.compile(r"^##\s+Notes\s*$", re.MULTILINE)


def _hash_notes_section(path: Path) -> str | None:
    """SHA256 of the '## Notes'-to-EOF section of `path`, or None.

    Absent file / absent heading / unreadable all map to None — the
    clobber invariant only engages when there is a stable hash to compare.
    Mirrors the news_research helper.
    """
    if not path.is_file():
        return None
    try:
        body = path.read_text()
    except (OSError, UnicodeDecodeError):
        log.warning("Could not read %s for notes-hash snapshot", path,
                    exc_info=True)
        return None
    m = _NOTES_HEADING_RE.search(body)
    section = body[m.start():] if m else ""
    return hashlib.sha256(section.encode("utf-8")).hexdigest()


async def run_for_iso_week(
    iso_week: str,
    *,
    db: NewsWeeklyStateDB,
    config: RunnerConfig,
    run_agent: AgentRunFn,
    recent_ratings: RatingsFn,
    notify: NotifyFn,
) -> None:
    """Execute one weekly pass for `iso_week` (e.g. '2026-20').

    Pure orchestration: corpus gate, deterministic recurrence pass, state
    transitions, weekly-note Notes-clobber guard, retry, Telegram ping on
    success. Fully independent of the daily chain. The initial insert_run
    is outside the guard (a DB fault there is a hard failure); every later
    failure is caught and recorded as `failed`.
    """
    await db.insert_run(iso_week)
    try:
        await _run_inner(
            iso_week, db=db, config=config, run_agent=run_agent,
            recent_ratings=recent_ratings, notify=notify,
        )
    except Exception as exc:
        log.exception(
            "Unexpected error in news_weekly.run_for_iso_week for %s",
            iso_week,
        )
        try:
            await db.update_run(
                iso_week, status="failed", error=f"unexpected: {exc}",
            )
        except Exception:
            log.exception(
                "Could not mark %s weekly failed after unexpected error",
                iso_week,
            )


async def _run_inner(
    iso_week: str,
    *,
    db: NewsWeeklyStateDB,
    config: RunnerConfig,
    run_agent: AgentRunFn,
    recent_ratings: RatingsFn,
    notify: NotifyFn,
) -> None:
    week_dates = iso_week_dates(iso_week)
    masters: dict[date, str] = {}
    for d in week_dates:
        p = _master_path(config.vault_root, d)
        if p.is_file():
            try:
                masters[d] = p.read_text()
            except (OSError, UnicodeDecodeError):
                log.warning("Could not read master %s; excluding", p,
                            exc_info=True)

    if len(masters) < config.min_days:
        log.info(
            "Only %d/%d masters for %s — insufficient corpus",
            len(masters), config.min_days, iso_week,
        )
        await db.update_run(
            iso_week, status="skipped_insufficient_corpus",
        )
        return

    threads = rank_recurring_threads(
        masters, recurrence_threshold=config.recurrence_threshold,
    )
    recurrence_block = render_recurrence_block(threads=threads)

    window_end = week_dates[-1]
    window_start = week_dates[0]
    try:
        ratings = await recent_ratings(window_start, window_end)
    except Exception:
        log.warning(
            "recent_ratings provider failed for %s; proceeding with none",
            iso_week, exc_info=True,
        )
        ratings = []
    ratings_block = render_ratings_block(ratings=ratings)

    prompt = build_runner_prompt(
        iso_week=iso_week,
        week_dates=week_dates,
        recurrence_block=recurrence_block,
        ratings_block=ratings_block,
        max_threads=config.max_threads,
    )

    note_path = _weekly_note_path(config.vault_root, iso_week)
    notes_hash_before = _hash_notes_section(note_path)

    last_error: str | None = None
    backoffs = config.retry_backoff_seconds or (0.0,)
    for attempt, wait_s in enumerate(backoffs, start=1):
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        try:
            output = await run_agent(AgentRunInput(
                iso_week=iso_week,
                vault_root=config.vault_root,
                model=config.model,
                max_threads=config.max_threads,
                max_turns=config.max_turns,
                prompt=prompt,
            ))
        except Exception as exc:
            log.exception(
                "Weekly agent raised attempt %d/%d for %s",
                attempt, len(backoffs), iso_week,
            )
            last_error = f"agent_exception: {exc}"
            continue

        if not output.success:
            last_error = output.error
            continue

        if notes_hash_before is not None:
            if _hash_notes_section(note_path) != notes_hash_before:
                await db.update_run(
                    iso_week, status="failed_notes_clobbered",
                    error="weekly agent modified ## Notes section",
                )
                log.error(
                    "Weekly %s rejected: ## Notes section modified",
                    iso_week,
                )
                return

        await db.update_run(
            iso_week, status="completed",
            threads_written=output.threads_written,
            entities_enriched=output.entities_enriched,
            cost_usd=output.cost_usd,
            turns_used=output.turns_used,
        )
        log.info(
            "Weekly completed for %s: %d threads, %d entities, "
            "%s turns, $%s",
            iso_week, output.threads_written, output.entities_enriched,
            output.turns_used, output.cost_usd,
        )
        try:
            await notify(
                f"📰 Weekly patterns — {iso_week}\n"
                f"{output.threads_written} recurring thread(s) surfaced.\n"
                f"obsidian://open?vault=PKM&file="
                f"01-Projects%2FNews%2Fweekly%2F{iso_week}",
            )
        except Exception:
            log.exception("Failed to send weekly patterns ping for %s",
                          iso_week)
        return

    await db.update_run(
        iso_week, status="failed", error=last_error or "unknown",
    )
    log.error(
        "Weekly failed for %s after %d attempts: %s",
        iso_week, len(backoffs), last_error,
    )


_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_agent_summary(
    text_parts: list[str], *, turns_used: int, cost_usd: float | None,
) -> AgentRunOutput:
    """Pull the LAST fenced ```json``` block out of the agent's text."""
    joined = "\n".join(text_parts)
    matches = _JSON_BLOCK_RE.findall(joined)
    if not matches:
        return AgentRunOutput(
            success=False, turns_used=turns_used, cost_usd=cost_usd,
            error="no JSON summary block in agent output", text=joined,
        )
    try:
        data = json.loads(matches[-1])
    except json.JSONDecodeError as exc:
        return AgentRunOutput(
            success=False, turns_used=turns_used, cost_usd=cost_usd,
            error=f"invalid JSON summary: {exc}", text=joined,
        )
    success = bool(data.get("success", False))
    error = None if success else (
        data.get("error")
        or "agent reported success=false without an error message"
    )
    return AgentRunOutput(
        success=success,
        threads_written=int(data.get("threads_written", 0)),
        entities_enriched=int(data.get("entities_enriched", 0)),
        turns_used=turns_used,
        cost_usd=cost_usd,
        text=joined,
        error=error,
    )


async def run_agent_via_agent_infra(
    inp: AgentRunInput,
    *,
    mcp_server_path: str | None = None,
) -> AgentRunOutput:
    """Concrete AgentRunFn — invokes the news-weekly-patterns skill.

    Captures cost via an on_event closure (AgentLoopResult does not carry
    cost_usd; only the streaming 'complete' TraceEvent does). Mirrors
    news_research.run_agent_via_agent_infra.
    """
    options = build_agent_options(
        system_prompt=(
            "You are the news weekly pattern agent. Follow the "
            "news-weekly-patterns skill exactly."
        ),
        pkm_vault_path=inp.vault_root,
        model=inp.model,
        max_turns=inp.max_turns,
        setting_sources=["project"],
        allow_skill_tool=True,
        mcp_server_path=mcp_server_path,
    )

    cost_holder: dict[str, float | None] = {"cost_usd": None}

    async def _on_event(event: TraceEvent) -> None:
        if event.kind == "complete" and event.cost_usd is not None:
            cost_holder["cost_usd"] = event.cost_usd

    result: AgentLoopResult = await run_agent_loop_streaming(
        inp.prompt, options, _on_event,
    )
    if result.error:
        return AgentRunOutput(
            success=False,
            turns_used=result.turns_used,
            cost_usd=cost_holder["cost_usd"],
            error=result.error,
            text="\n".join(result.text_parts),
        )
    return _parse_agent_summary(
        result.text_parts,
        turns_used=result.turns_used,
        cost_usd=cost_holder["cost_usd"],
    )
