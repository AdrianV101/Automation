from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from .clippings_router import route_clipping
from .clippings_state import ClippingsStateDB, clipping_key, parse_clipping

log = logging.getLogger(__name__)


async def wait_until_stable(
    path: Path, *, settle_s: float, poll_s: float, timeout_s: float,
) -> bool:
    """True once (size, mtime) is unchanged for `settle_s`.

    Guards against partial Obsidian Sync writes. False if the file
    vanishes or never settles within `timeout_s`.
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    last: tuple[int, float] | None = None
    stable_since: float | None = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            st = path.stat()
        except FileNotFoundError:
            return False
        sig = (st.st_size, st.st_mtime)
        now = asyncio.get_event_loop().time()
        if sig == last:
            if stable_since is not None and (now - stable_since) >= settle_s:
                return True
        else:
            last = sig
            stable_since = now
        await asyncio.sleep(poll_s)
    return False


# Terminal statuses: clipping already handled, never re-route.
_TERMINAL = {"routed", "skipped"}


async def process_clipping(
    path: Path,
    *,
    vault_root: Path,
    state: ClippingsStateDB,
    telegram_notifier: Callable[..., Awaitable[None]],
    list_routing_targets: Callable[[], list[str]],
    send_clarification: Callable[..., Awaitable[int | None]] | None = None,
    news_topic_id: int | None = None,
    model: str = "claude-opus-4-7",
) -> None:
    """Gate one clipping through the state machine and route it.

    Owns all side effects: state transitions, file is left in place on
    failure/ambiguity, Telegram summary/clarification. The router owns the
    vault mutations (move/link/attach) for the confident path.
    """
    try:
        frontmatter, body = parse_clipping(path)
    except OSError:
        log.exception("Cannot read clipping %s", path)
        return
    if not frontmatter and not body.strip():
        log.warning("Empty/unparseable clipping %s — marking failed", path.name)
        key = clipping_key(frontmatter, body)
        await state.insert_pending(key, path.name)
        await state.mark_failed(key)
        await telegram_notifier(
            f"⚠️ Clipping could not be parsed: {path.name} (left in Clippings/)",
            thread_id=news_topic_id,
        )
        return

    key = clipping_key(frontmatter, body)
    existing = await state.get(key)
    if existing is not None:
        if existing["status"] in _TERMINAL:
            log.info("Clipping %s already %s — skipping", path.name, existing["status"])
            return
        if existing["status"] == "pending_clarification" and existing["telegram_message_id"]:
            log.info("Clipping %s awaiting clarification — skipping", path.name)
            return
    else:
        await state.insert_pending(key, path.name)

    outcome = await route_clipping(
        path, vault_root,
        routing_targets=list_routing_targets(),
        model=model,
    )

    if outcome.kind == "routed":
        await state.mark_routed(key, outcome.routed_path or "")
        plan_note = f"\nPlan updated: {outcome.plan_attached}" if outcome.plan_attached else ""
        await telegram_notifier(
            f"📎 Clipping filed: {path.name}\n→ {outcome.routed_path}\n"
            f"Links added: {outcome.links_added}{plan_note}",
            thread_id=news_topic_id,
        )
    elif outcome.kind == "needs_clarification":
        msg_id: int | None = None
        if send_clarification is not None:
            msg_id = await send_clarification(
                question=outcome.question or "Where should this clipping go?",
                candidates=outcome.candidates,
                clipping_name=path.name,
            )
        await state.set_pending_clarification(
            key, candidates=outcome.candidates, telegram_message_id=msg_id,
        )
    else:  # failed
        await state.mark_failed(key)
        await telegram_notifier(
            f"⚠️ Clipping routing failed: {path.name}\n{outcome.error}\n"
            f"(left in Clippings/, will retry)",
            thread_id=news_topic_id,
        )


async def reconcile_clippings(
    *,
    clippings_dir: Path,
    vault_root: Path,
    state: ClippingsStateDB,
    telegram_notifier: Callable[..., Awaitable[None]],
    list_routing_targets: Callable[[], list[str]],
    send_clarification: Callable[..., Awaitable[int | None]] | None,
    max_failed_retries: int = 3,
    news_topic_id: int | None = None,
    model: str = "claude-opus-4-7",
) -> None:
    """Scan the folder; (re)process anything not in a terminal/awaiting state.

    Safety net behind the watcher: recovers clippings synced while the
    daemon was down, and retries `failed` rows up to `max_failed_retries`.
    """
    if not clippings_dir.is_dir():
        return
    for path in sorted(clippings_dir.glob("*.md")):
        try:
            fm, body = parse_clipping(path)
        except OSError:
            log.exception("Reconcile: cannot read %s", path)
            continue
        key = clipping_key(fm, body)
        row = await state.get(key)
        if row is not None:
            if row["status"] in _TERMINAL:
                continue
            if row["status"] == "pending_clarification" and row["telegram_message_id"]:
                continue
            if row["status"] == "failed" and row["retry_count"] >= max_failed_retries:
                continue
        await process_clipping(
            path, vault_root=vault_root, state=state,
            telegram_notifier=telegram_notifier,
            list_routing_targets=list_routing_targets,
            send_clarification=send_clarification,
            news_topic_id=news_topic_id, model=model,
        )
