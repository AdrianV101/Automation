from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from agent_infra import build_agent_options, run_agent_loop_streaming

from .prompt import build_runner_prompt
from .state import NewsDailyMasterStateDB

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunnerConfig:
    vault_root: Path
    model: str = "claude-opus-4-7"
    telegram_topic_id: int | None = None
    retry_backoff_seconds: tuple[float, ...] = (0.0, 60.0, 300.0)


@dataclass
class AgentRunInput:
    """Inputs to the agent runner callable; lets us inject a fake in tests."""
    target_date: date
    vault_root: Path
    model: str


@dataclass
class AgentRunOutput:
    """Structured summary returned by the agent runner.

    Enforces the success/error pair invariant: success implies error is None;
    failure requires a non-None error. This mirrors `AgentRoutingResult` in
    extraction.py and prevents the silent-failure mode where the runner reads
    `output.error or "fallback"` and silently substitutes when error is None.
    """
    success: bool
    item_count: int
    categories: list[str] = field(default_factory=list)
    new_categories: list[str] = field(default_factory=list)
    skipped_items: list[str] = field(default_factory=list)
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
    """SHA256 of the '## Notes' section of the master doc.

    Returns None if the master doc is absent OR unreadable. The unreadable
    case is treated identically to absent on purpose: the clobber-detection
    invariant only kicks in when we have a stable hash to compare against.
    A non-UTF8 payload or a permissions issue that blocks the read is
    surprising enough to log, but not worth crashing the whole daily run for.
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

    try:
        await _run_for_date_inner(
            target_date, db=db, config=config, run_agent=run_agent, notify=notify,
        )
    except Exception as exc:
        # Catch-all so the row never gets stuck in 'running'. Inner branches
        # are responsible for the typed-failure transitions (failed,
        # failed_verification, failed_notes_clobbered); anything that reaches
        # here is unexpected (DB error, IO error in a helper, programmer bug).
        log.exception("Unexpected error in run_for_date for %s", target_date)
        try:
            await db.update_run(
                target_date, status="failed", error=f"unexpected: {exc}",
            )
        except Exception:
            log.exception(
                "Could not mark %s failed after unexpected error", target_date,
            )
        try:
            await notify(
                f"⚠️ News daily master crashed for {target_date.isoformat()}: "
                f"{exc}",
            )
        except Exception:
            log.exception("Could not notify after unexpected error in %s", target_date)


async def _run_for_date_inner(
    target_date: date,
    *,
    db: NewsDailyMasterStateDB,
    config: RunnerConfig,
    run_agent: AgentRunFn,
    notify: NotifyFn,
) -> None:
    """The body of run_for_date, wrapped above for stuck-row defence."""
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
            # Pair invariant guarantees output.error is non-None here.
            last_error = output.error
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

        # Verification: agent's own self-check reports any skipped items.
        if output.skipped_items:
            await db.update_run(
                target_date, status="failed_verification",
                error="agent skipped items: " + "; ".join(output.skipped_items),
            )
            await notify(
                f"⚠️ News master doc for {target_date.isoformat()} "
                f"completed with skipped items:\n"
                + "\n".join(f"- {s}" for s in output.skipped_items),
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


# ---------------------------------------------------------------------------
# Concrete AgentRunFn — wires agent_infra to the news-daily-master skill
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_agent_summary(text_parts: list[str]) -> AgentRunOutput:
    """Pull a structured summary out of the agent's text blocks.

    Looks for the LAST fenced ```json``` block in the concatenated text.
    The skill instructs the agent to emit one as its final action.
    """
    joined = "\n".join(text_parts)
    matches = _JSON_BLOCK_RE.findall(joined)
    if not matches:
        return AgentRunOutput(
            success=False, item_count=0,
            error="no JSON summary block in agent output",
            text=joined,
        )
    try:
        data = json.loads(matches[-1])
    except json.JSONDecodeError as exc:
        return AgentRunOutput(
            success=False, item_count=0,
            error=f"invalid JSON summary: {exc}", text=joined,
        )
    success = bool(data.get("success", False))
    raw_error = data.get("error")
    # Honour the AgentRunOutput pair invariant: drop any error if success=True
    # (agents occasionally include diagnostic 'error' fields on a successful
    # run); synthesise a fallback if success=False and no error was provided.
    if success:
        error = None
    else:
        error = raw_error or "agent reported success=false without an error message"
    return AgentRunOutput(
        success=success,
        item_count=int(data.get("item_count", 0)),
        categories=list(data.get("categories", [])),
        new_categories=list(data.get("new_categories", [])),
        skipped_items=list(data.get("skipped_items", [])),
        text=joined,
        error=error,
    )


async def run_agent_via_agent_infra(
    inp: AgentRunInput,
    *,
    mcp_server_path: str | None = None,
) -> AgentRunOutput:
    """Concrete AgentRunFn that invokes the news-daily-master skill via Agent SDK.

    System prompt is intentionally minimal: the procedural detail lives in
    the skill, loaded via setting_sources=["project"].
    """
    options = build_agent_options(
        system_prompt=(
            "You are the news daily master agent. Follow the "
            "news-daily-master skill exactly."
        ),
        pkm_vault_path=inp.vault_root,
        model=inp.model,
        setting_sources=["project"],
        allow_skill_tool=True,
        mcp_server_path=mcp_server_path,
    )
    prompt = build_runner_prompt(inp.target_date)
    result = await run_agent_loop_streaming(prompt, options)
    if result.error:
        return AgentRunOutput(
            success=False, item_count=0, error=result.error,
            text="\n".join(result.text_parts),
        )
    return _parse_agent_summary(result.text_parts)
