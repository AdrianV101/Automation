from __future__ import annotations

import asyncio
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
    # First entry is the wait before attempt 1 (always 0); subsequent entries
    # gate retries. Default = one retry after 60s, matching the design's
    # error-handling table.
    agent_retry_backoff_seconds: tuple[float, ...] = (0.0, 60.0)


@dataclass(frozen=True)
class AgentRunInput:
    target_date: date
    vault_root: Path
    model: str
    prompt: str


@dataclass
class AgentRunOutput:
    """Structured summary returned by the digest agent.

    Pair invariant: success=True implies error is None; success=False
    requires a non-None error. Lets callers branch on `success` without
    a `or "fallback"` pattern that silently masks missing errors.
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
# Position-preserving: result list MUST be the same length as the input list;
# entry i is the message_id of input message i, or None if that send failed.
# The runner relies on positional alignment to attach message_ids to the
# correct slice of items when categories span multiple messages.
SendMessagesFn = Callable[
    [list[tuple[str, dict]]], Awaitable[list[int | None]],
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

    # 1. Build the recent-feedback prompt block.
    window_start = target_date - timedelta(days=config.feedback_window_days)
    rating_rows = await db.recent_ratings(window_start, target_date)
    feedback_block = render_feedback_block(ratings=rating_rows)

    prompt = build_runner_prompt(
        target_date=target_date, feedback_block=feedback_block,
    )

    # 2. Invoke the agent with retry-on-exception. The first backoff entry
    # is 0 (no wait before attempt 1). A returned-failure AgentRunOutput
    # is treated as a verification failure and does NOT retry — only
    # raised exceptions (network, model, MCP transport) trigger a retry.
    backoffs = config.agent_retry_backoff_seconds or (0.0,)
    output: AgentRunOutput | None = None
    last_exc: BaseException | None = None
    for attempt, wait_s in enumerate(backoffs, start=1):
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        try:
            output = await run_agent(AgentRunInput(
                target_date=target_date,
                vault_root=config.vault_root,
                model=config.model,
                prompt=prompt,
            ))
            break
        except Exception as exc:
            log.exception(
                "Agent run raised on attempt %d/%d for digest %s",
                attempt, len(backoffs), target_date,
            )
            last_exc = exc

    if output is None:
        await db.update_run(
            target_date, status="failed",
            error=f"agent_exception: {last_exc}",
        )
        await notify(
            f"⚠️ News personal digest agent failed for "
            f"{target_date.isoformat()} after "
            f"{len(backoffs)} attempt(s): {last_exc}",
        )
        return

    if not output.success:
        await db.update_run(
            target_date, status="failed_verification",
            error=output.error,
        )
        await notify(
            f"⚠️ News personal digest verification failed for "
            f"{target_date.isoformat()}: {output.error}",
        )
        return

    # 3. Persist items, capture autoincrement ids, rebuild category objects
    #    with real ids so the rendered button payloads reference DB rows.
    persisted_categories: list[DigestCategory] = []
    for cat in output.categories:
        new_items: list[DigestItem] = []
        for it in cat.items:
            new_id = await db.insert_item(
                digest_date=target_date,
                item=DigestItemInput(
                    source_path=it.source_path,
                    category=cat.name,
                    title=it.title,
                    url=it.url,
                    position=it.position,
                ),
            )
            new_items.append(DigestItem(
                id=new_id,
                title=it.title, url=it.url,
                briefing=it.briefing, why_you_care=it.why_you_care,
                source_path=it.source_path, position=it.position,
            ))
        persisted_categories.append(DigestCategory(
            name=cat.name, emoji=cat.emoji, items=new_items,
        ))

    # 4. Render and send Telegram messages, attach message_ids.
    # The sender returns a positional list of len(messages) where None
    # marks a failed send. We advance the item offset by row_count on
    # every message (success OR failure) so item ↔ message_id pairing
    # stays aligned across categories — failed-send items simply never
    # get a telegram_message_id attached, which the callback handler
    # treats as a stale tap (Item expired toast, no keyboard wipe).
    messages = build_messages(persisted_categories)
    failed_sends = 0
    if messages:
        message_ids = await send_messages(messages)
        if len(message_ids) != len(messages):
            # Misuse from the injected sender — refuse to attach anything
            # rather than silently mis-route. Mark the run failed.
            await db.update_run(
                target_date, status="failed",
                error=(
                    f"send_messages returned {len(message_ids)} ids for "
                    f"{len(messages)} messages (length mismatch)"
                ),
            )
            await notify(
                f"⚠️ News personal digest delivery for "
                f"{target_date.isoformat()} failed: sender returned the "
                f"wrong number of message_ids."
            )
            return

        flat_items = [
            it for cat in persisted_categories for it in cat.items
        ]
        offset = 0
        for (_, kb), msg_id in zip(messages, message_ids):
            row_count = len(kb["inline_keyboard"])
            if msg_id is None:
                failed_sends += 1
            else:
                ids_in_msg = [
                    it.id for it in flat_items[offset:offset + row_count]
                ]
                await db.attach_telegram_message_id(
                    item_ids=ids_in_msg, telegram_message_id=msg_id,
                )
            offset += row_count

    # 5. Mark completed; surface partial delivery to the operator.
    # Partial delivery is degraded but not catastrophic — items that did
    # get delivered carry their telegram_message_id and remain rateable.
    summary = output.rating_signal_summary
    if failed_sends > 0:
        summary = (
            f"{summary} (delivery: {len(messages) - failed_sends}/"
            f"{len(messages)} messages sent)"
        )
        await notify(
            f"⚠️ News personal digest for {target_date.isoformat()} "
            f"partially delivered: {failed_sends} of {len(messages)} "
            f"messages failed to send. See daemon log for details."
        )
    await db.update_run(
        target_date, status="completed",
        item_count=output.item_count,
        rating_signal_summary=summary,
    )


# ---------------------------------------------------------------------------
# Agent summary parser + concrete agent_infra adapter
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_MIN_BRIEFING_CHARS = 80


def parse_agent_summary(text_parts: list[str]) -> AgentRunOutput:
    """Pull a structured summary out of the agent's text blocks.

    Looks for the LAST fenced ```json``` block. Verifies all items have a
    minimum briefing length (≥80 chars) per the design's verification
    checklist.
    """
    joined = "\n".join(text_parts)
    matches = _JSON_BLOCK_RE.findall(joined)
    if not matches:
        return AgentRunOutput(
            success=False,
            error="no JSON summary block in agent output",
            text=joined,
        )
    try:
        data = json.loads(matches[-1])
    except json.JSONDecodeError as exc:
        return AgentRunOutput(
            success=False,
            error=f"invalid JSON summary: {exc}",
            text=joined,
        )

    success = bool(data.get("success", False))
    raw_error = data.get("error")
    if not success:
        return AgentRunOutput(
            success=False,
            error=raw_error or "agent reported success=false without an error",
            text=joined,
        )

    try:
        categories: list[DigestCategory] = []
        for cat_data in data.get("categories", []):
            items: list[DigestItem] = []
            for pos, it in enumerate(cat_data.get("items", []), start=1):
                briefing = it.get("briefing") or ""
                if len(briefing) < _MIN_BRIEFING_CHARS:
                    return AgentRunOutput(
                        success=False,
                        error=(
                            f"briefing too short ({len(briefing)} chars) "
                            f"for item {it.get('title')!r} in category "
                            f"{cat_data.get('name')!r}"
                        ),
                        text=joined,
                    )
                items.append(DigestItem(
                    id=0,  # populated later by runner.insert_item
                    title=str(it["title"]),
                    url=it.get("url"),
                    briefing=briefing,
                    why_you_care=str(it["why_you_care"]),
                    source_path=str(it["source_path"]),
                    position=pos,
                ))
            categories.append(DigestCategory(
                name=str(cat_data["name"]),
                emoji=str(cat_data.get("emoji", "•")),
                items=items,
            ))
    except (KeyError, TypeError) as exc:
        return AgentRunOutput(
            success=False, error=f"malformed item: {exc}", text=joined,
        )

    return AgentRunOutput(
        success=True,
        categories=categories,
        rating_signal_summary=str(data.get("rating_signal_summary", "")),
        text=joined,
    )


async def run_agent_via_agent_infra(
    inp: AgentRunInput,
    *,
    mcp_server_path: str | None = None,
) -> AgentRunOutput:
    """Concrete AgentRunFn that invokes the news-personal-digest skill.

    System prompt is intentionally minimal: procedural detail lives in
    .claude/skills/news-personal-digest/SKILL.md, loaded via
    setting_sources=["project"].
    """
    options = build_agent_options(
        system_prompt=(
            "You are the news personal digest agent. Follow the "
            "news-personal-digest skill exactly."
        ),
        pkm_vault_path=inp.vault_root,
        model=inp.model,
        setting_sources=["project"],
        allow_skill_tool=True,
        mcp_server_path=mcp_server_path,
    )
    result = await run_agent_loop_streaming(inp.prompt, options)
    if result.error:
        return AgentRunOutput(
            success=False, error=result.error,
            text="\n".join(result.text_parts),
        )
    return parse_agent_summary(result.text_parts)
