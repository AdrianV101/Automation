"""Hacker News source adapter — Firebase poll → vault notes via news_pipeline.

Sibling to `news_email_adapter.py`. The HTTP client lives in `hn_client`,
the dedupe state in `hn_state`. This module owns: HN-dict → NewsItem
conversion, top-N filtering, the per-poll orchestration, the Telegram
summary builder, and the daemon-side scheduling loop.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from markdownify import markdownify as _markdownify
from news_pipeline import NewsItem, write_news_item

from .hn_state import HackerNewsStateDB

log = logging.getLogger(__name__)

HN_ITEM_URL_TEMPLATE = "https://news.ycombinator.com/item?id={item_id}"


def _render_hn_text(html: str) -> str:
    """HN `text` is escaped HTML with a small tag set (<p>, <i>, <a>, etc.)."""
    soup = BeautifulSoup(html, "html.parser")
    return _markdownify(str(soup), heading_style="ATX").strip()


def _build_body(hn_item: dict[str, Any]) -> str:
    item_id = hn_item["id"]
    title = hn_item.get("title") or ""
    score = hn_item.get("score") or 0
    descendants = hn_item.get("descendants") or 0
    author = hn_item.get("by") or ""
    url = hn_item.get("url")
    text_html = hn_item.get("text")

    hn_url = HN_ITEM_URL_TEMPLATE.format(item_id=item_id)

    parts: list[str] = []

    if text_html:
        parts.append(_render_hn_text(text_html))
        parts.append("")
        parts.append("---")
        parts.append("")

    if url:
        parts.append(f"[{title}]({url})")
        parts.append("")

    parts.append(f"- **Points:** {score}")
    parts.append(f"- **Comments:** [{descendants} on HN]({hn_url})")
    if author:
        parts.append(f"- **Submitted by:** @{author}")

    return "\n".join(parts).rstrip() + "\n"


def _to_news_item(hn_item: dict[str, Any]) -> NewsItem:
    item_id = int(hn_item["id"])
    return NewsItem(
        message_id=f"hn-{item_id}",
        source="Hacker News",
        source_type="hacker-news",
        source_address=HN_ITEM_URL_TEMPLATE.format(item_id=item_id),
        subject=hn_item.get("title") or "",
        received_at=datetime.fromtimestamp(int(hn_item["time"]), tz=timezone.utc),
        body_md=_build_body(hn_item),
        extra={
            "hn-id": item_id,
            "hn-points": int(hn_item.get("score") or 0),
            "hn-comments": int(hn_item.get("descendants") or 0),
            "hn-author": hn_item.get("by") or "",
        },
    )


def _filter_items(
    items: list[dict[str, Any]],
    *,
    min_points: int,
    max_items: int,
) -> list[dict[str, Any]]:
    eligible: list[dict[str, Any]] = []
    for it in items:
        if it.get("dead") or it.get("deleted"):
            continue
        if it.get("type") != "story":
            continue
        score = it.get("score") or 0
        if score < min_points:
            continue
        eligible.append(it)
    eligible.sort(
        key=lambda x: (
            -(x.get("score") or 0),    # score desc
            -(x.get("time") or 0),     # time desc (newer first)
            int(x.get("id") or 0),     # id asc (deterministic)
        ),
    )
    return eligible[:max_items]


@dataclass(frozen=True)
class PollSummary:
    ingested: int
    skipped_dedupe: int
    fetch_failures: int
    write_failures: int
    top_title: str
    top_points: int


async def run_poll_cycle(
    client,
    state_db: HackerNewsStateDB,
    vault_root: Path,
    *,
    min_points: int,
    max_items: int,
    topstories_pool: int,
) -> PollSummary:
    """One pass: top IDs -> fetch -> filter -> write+record per new item."""
    top_ids = await client.top_story_ids(limit=topstories_pool)
    raw_items = await client.get_items(top_ids)
    fetch_failures = len(top_ids) - len(raw_items)
    selected = _filter_items(
        raw_items, min_points=min_points, max_items=max_items,
    )
    ingested = 0
    skipped_dedupe = 0
    write_failures = 0
    top_title = ""
    top_points = 0
    if selected:
        top_title = selected[0].get("title") or ""
        top_points = int(selected[0].get("score") or 0)

    for hn_item in selected:
        item_id = int(hn_item["id"])
        key = f"hn-{item_id}"
        if await state_db.is_processed(key):
            skipped_dedupe += 1
            continue
        try:
            news_item = _to_news_item(hn_item)
            vault_path = write_news_item(news_item, vault_root)
        except Exception:
            log.exception("HN write failed for item_id=%d -- will retry tomorrow", item_id)
            write_failures += 1
            continue
        try:
            await state_db.record_processed_full(
                item_id,
                points=int(hn_item.get("score") or 0),
                title=hn_item.get("title"),
                vault_path=str(vault_path.relative_to(vault_root)),
            )
        except Exception:
            log.exception(
                "HN record_processed_full failed for item_id=%d "
                "(vault note already written; dedup will hit next poll via INSERT-OR-IGNORE)",
                item_id,
            )
        ingested += 1

    if top_ids:
        await state_db.set_checkpoint(max(top_ids))

    return PollSummary(
        ingested=ingested,
        skipped_dedupe=skipped_dedupe,
        fetch_failures=fetch_failures,
        write_failures=write_failures,
        top_title=top_title,
        top_points=top_points,
    )
