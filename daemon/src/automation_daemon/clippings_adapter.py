from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

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


class _ClippingHandler(FileSystemEventHandler):
    """Pushes newly created/moved *.md paths onto an asyncio queue."""

    def __init__(self, queue: "asyncio.Queue[Path]", loop: asyncio.AbstractEventLoop) -> None:
        self._queue = queue
        self._loop = loop

    def _enqueue(self, raw_path: str) -> None:
        p = Path(raw_path)
        if p.suffix == ".md":
            self._loop.call_soon_threadsafe(self._queue.put_nowait, p)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue(event.dest_path)


async def run_clippings_watch_loop(
    *,
    clippings_dir: Path,
    vault_root: Path,
    state: ClippingsStateDB,
    telegram_notifier: Callable[..., Awaitable[None]],
    list_routing_targets: Callable[[], list[str]],
    send_clarification: Callable[..., Awaitable[int | None]] | None,
    settle_s: float = 5.0,
    poll_s: float = 0.5,
    timeout_s: float = 60.0,
    reconcile_interval_s: float = 3600.0,
    max_failed_retries: int = 3,
    news_topic_id: int | None = None,
    model: str = "claude-opus-4-7",
    _stop_event: asyncio.Event | None = None,
) -> None:
    """Long-running task: watchdog observer + debounce + periodic reconcile.

    Crash-safe: wrapped by supervise() in the orchestrator. `_stop_event`
    exists for tests; production passes None (runs until cancelled).
    """
    clippings_dir.mkdir(parents=True, exist_ok=True)
    stop = _stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Path] = asyncio.Queue()

    async def _reconcile() -> None:
        await reconcile_clippings(
            clippings_dir=clippings_dir, vault_root=vault_root, state=state,
            telegram_notifier=telegram_notifier,
            list_routing_targets=list_routing_targets,
            send_clarification=send_clarification,
            max_failed_retries=max_failed_retries,
            news_topic_id=news_topic_id, model=model,
        )

    async def _periodic_reconcile() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=reconcile_interval_s)
            except asyncio.TimeoutError:
                await _reconcile()

    observer = Observer()
    observer.schedule(_ClippingHandler(queue, loop), str(clippings_dir), recursive=False)
    periodic: asyncio.Task | None = None
    try:
        observer.start()
        # Start watching BEFORE the initial reconcile so files dropped
        # during the reconcile scan are queued (not lost); process_clipping
        # dedupes the overlap on clipping_key.
        await _reconcile()  # startup catch-up
        periodic = asyncio.create_task(_periodic_reconcile())
        while not stop.is_set():
            try:
                path = await asyncio.wait_for(queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if not await wait_until_stable(
                path, settle_s=settle_s, poll_s=poll_s, timeout_s=timeout_s,
            ):
                log.warning("Clipping %s never settled — leaving for reconcile", path.name)
                continue
            try:
                await process_clipping(
                    path, vault_root=vault_root, state=state,
                    telegram_notifier=telegram_notifier,
                    list_routing_targets=list_routing_targets,
                    send_clarification=send_clarification,
                    news_topic_id=news_topic_id, model=model,
                )
            except Exception:
                log.exception(
                    "Unhandled error processing clipping %s — skipping, "
                    "leaving for reconcile", path.name,
                )
    finally:
        observer.stop()
        observer.join(timeout=5)
        if observer.is_alive():
            log.error(
                "Clippings observer thread did not stop within 5s — "
                "leaking the filesystem watch on %s", clippings_dir,
            )
        if periodic is not None:
            periodic.cancel()
            try:
                await periodic
            except asyncio.CancelledError:
                pass
