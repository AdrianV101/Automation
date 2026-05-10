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

    # 1. Build the recent-feedback prompt block.
    window_start = target_date - timedelta(days=config.feedback_window_days)
    rating_rows = await db.recent_ratings(window_start, target_date)
    feedback_block = render_feedback_block(ratings=rating_rows)

    prompt = build_runner_prompt(
        target_date=target_date, feedback_block=feedback_block,
    )

    # 2. Invoke the agent.
    try:
        output = await run_agent(AgentRunInput(
            target_date=target_date,
            vault_root=config.vault_root,
            model=config.model,
            prompt=prompt,
        ))
    except Exception as exc:
        log.exception("Agent run raised for digest %s", target_date)
        await db.update_run(
            target_date, status="failed",
            error=f"agent_exception: {exc}",
        )
        await notify(
            f"⚠️ News personal digest agent failed for "
            f"{target_date.isoformat()}: {exc}",
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
    messages = build_messages(persisted_categories)
    if messages:
        message_ids = await send_messages(messages)
        # `messages` and `message_ids` are aligned 1:1; each message's
        # button rows correspond to a contiguous slice of items in the
        # flattened persisted_categories.items list.
        flat_items = [
            it for cat in persisted_categories for it in cat.items
        ]
        offset = 0
        for (_, kb), msg_id in zip(messages, message_ids):
            row_count = len(kb["inline_keyboard"])
            ids_in_msg = [it.id for it in flat_items[offset:offset + row_count]]
            await db.attach_telegram_message_id(
                item_ids=ids_in_msg, telegram_message_id=msg_id,
            )
            offset += row_count

    # 5. Mark completed.
    await db.update_run(
        target_date, status="completed",
        item_count=output.item_count,
        rating_signal_summary=output.rating_signal_summary,
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
