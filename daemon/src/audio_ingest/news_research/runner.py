from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from agent_infra import (
    AgentLoopResult,
    build_agent_options,
    run_agent_loop_streaming,
)
from agent_infra.runner import TraceEvent

from .prompt import build_runner_prompt, render_ratings_block
from .state import NewsResearchStateDB

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunnerConfig:
    vault_root: Path
    model: str = "claude-sonnet-4-6"
    max_items: int = 3
    max_turns: int = 60
    feedback_window_days: int = 7
    # First entry is the wait before attempt 1 (always 0); rest gate retries.
    retry_backoff_seconds: tuple[float, ...] = (0.0, 60.0)


@dataclass
class AgentRunInput:
    target_date: date
    vault_root: Path
    model: str
    max_items: int
    max_turns: int
    prompt: str


@dataclass
class AgentRunOutput:
    """Structured summary from the research agent.

    Enforces the success/error pair invariant: success implies error is None;
    failure requires a non-None error. This mirrors `AgentRoutingResult` in
    extraction.py and prevents the silent-failure mode where the runner reads
    `output.error or "fallback"` and silently substitutes when error is None.
    """
    success: bool
    items_researched: int = 0
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
# (start, end) inclusive -> joined rating rows; matches
# NewsDigestStateDB.recent_ratings. Injected so news_research stays
# decoupled from news_personal_digest (and works when digest is disabled).
RatingsFn = Callable[[date, date], Awaitable[list[dict[str, Any]]]]


def _master_doc_path(vault_root: Path, target_date: date) -> Path:
    return (
        vault_root / "01-Projects" / "News" / "daily"
        / f"{target_date.isoformat()}-master.md"
    )


_NOTES_HEADING_RE = re.compile(r"^##\s+Notes\s*$", re.MULTILINE)


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
    """SHA256 of the '## Notes' section, or None if absent/unreadable.

    Same contract as the master runner's helper: absent and unreadable are
    treated identically — the clobber invariant only engages when there is
    a stable hash to compare against.
    """
    if not master_path.is_file():
        return None
    try:
        body = master_path.read_text()
    except (OSError, UnicodeDecodeError):
        log.warning(
            "Could not read master doc for notes-hash snapshot at %s",
            master_path, exc_info=True,
        )
        return None
    section = _extract_notes_section(body)
    return hashlib.sha256(section.encode("utf-8")).hexdigest()


async def run_for_date(
    target_date: date,
    *,
    db: NewsResearchStateDB,
    config: RunnerConfig,
    run_agent: AgentRunFn,
    recent_ratings: RatingsFn,
) -> None:
    """Execute one research pass for `target_date`.

    Pure orchestration: master-doc gate, state transitions, Notes-clobber
    guard, retry. No Telegram — per the design (D5) research findings
    surface via the digest; failures are state-DB + log only. Never raises:
    research is best-effort enrichment and must not break the daily chain.
    """
    await db.insert_run(target_date)
    try:
        await _run_for_date_inner(
            target_date, db=db, config=config,
            run_agent=run_agent, recent_ratings=recent_ratings,
        )
    except Exception as exc:
        log.exception(
            "Unexpected error in news_research.run_for_date for %s",
            target_date,
        )
        try:
            await db.update_run(
                target_date, status="failed", error=f"unexpected: {exc}",
            )
        except Exception:
            log.exception(
                "Could not mark %s research failed after unexpected error",
                target_date,
            )


async def _run_for_date_inner(
    target_date: date,
    *,
    db: NewsResearchStateDB,
    config: RunnerConfig,
    run_agent: AgentRunFn,
    recent_ratings: RatingsFn,
) -> None:
    """The body of run_for_date, wrapped above for stuck-row defence."""
    master_path = _master_doc_path(config.vault_root, target_date)
    if not master_path.is_file():
        log.info(
            "No master doc for %s — skipping research", target_date,
        )
        await db.update_run(target_date, status="skipped_no_master")
        return

    notes_hash_before = _hash_notes_section(master_path)

    window_end = target_date
    window_start = target_date - timedelta(
        days=max(config.feedback_window_days - 1, 0),
    )
    try:
        ratings = await recent_ratings(window_start, window_end)
    except Exception:
        # Ratings are an optional signal; never fail research over them.
        log.warning(
            "recent_ratings provider failed for %s; proceeding with none",
            target_date, exc_info=True,
        )
        ratings = []
    ratings_block = render_ratings_block(ratings=ratings)
    prompt = build_runner_prompt(
        target_date=target_date,
        ratings_block=ratings_block,
        max_items=config.max_items,
    )

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
                max_items=config.max_items,
                max_turns=config.max_turns,
                prompt=prompt,
            ))
        except Exception as exc:
            log.exception(
                "Research agent raised on attempt %d/%d for %s",
                attempt, len(backoffs), target_date,
            )
            last_error = f"agent_exception: {exc}"
            continue

        if not output.success:
            last_error = output.error  # pair invariant: non-None
            continue

        if notes_hash_before is not None:
            notes_hash_after = _hash_notes_section(master_path)
            if notes_hash_after != notes_hash_before:
                await db.update_run(
                    target_date, status="failed_notes_clobbered",
                    error="research agent modified ## Notes section",
                )
                log.error(
                    "Research for %s rejected: ## Notes section modified",
                    target_date,
                )
                return

        await db.update_run(
            target_date, status="completed",
            items_researched=output.items_researched,
            cost_usd=output.cost_usd,
            turns_used=output.turns_used,
        )
        log.info(
            "Research completed for %s: %d items, %s turns, $%s",
            target_date, output.items_researched, output.turns_used,
            output.cost_usd,
        )
        return

    await db.update_run(
        target_date, status="failed", error=last_error or "unknown",
    )
    log.error(
        "Research failed for %s after %d attempts: %s",
        target_date, len(backoffs), last_error,
    )


# ---------------------------------------------------------------------------
# Concrete AgentRunFn — wires agent_infra to the news-research skill
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_agent_summary(
    text_parts: list[str], *, turns_used: int, cost_usd: float | None,
) -> AgentRunOutput:
    """Pull the LAST fenced ```json``` block out of the agent's text.

    The news-research skill instructs the agent to emit one as its final
    action: {"success": bool, "items_researched": int, "error"?: str}.
    """
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
    if success:
        error = None
    else:
        error = data.get("error") or (
            "agent reported success=false without an error message"
        )
    return AgentRunOutput(
        success=success,
        items_researched=int(data.get("items_researched", 0)),
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
    """Concrete AgentRunFn — invokes the news-research skill via Agent SDK.

    Captures cost via an on_event closure because AgentLoopResult does not
    carry cost_usd; only the streaming 'complete' TraceEvent does.
    """
    options = build_agent_options(
        system_prompt=(
            "You are the news research agent. Follow the news-research "
            "skill exactly."
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
