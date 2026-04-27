from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .bridge import BridgeConfig, ImapBridge, _BridgeConnection
from .errors import ImapAuthError, ImapConnectionError, MalformedEmailError
from .mime import parse_email
from .state import EmailIngestStateDB

log = logging.getLogger(__name__)

OnNewEmail = Callable[[int, bytes, dict[str, str]], Awaitable[None]]
OnPersistentFailure = Callable[[], Awaitable[None]]

IDLE_RENEW_SECONDS = 28 * 60
PERSISTENT_FAILURE_THRESHOLD = 3
AUTH_FAILURE_COOLDOWN_SECONDS = 300


@dataclass
class ImapIdleListener:
    cfg: BridgeConfig
    db: EmailIngestStateDB
    on_new_email: OnNewEmail
    on_persistent_failure: OnPersistentFailure | None = None
    auth_failure_cooldown: float = AUTH_FAILURE_COOLDOWN_SECONDS

    def __post_init__(self) -> None:
        self._bridge = ImapBridge(self.cfg)
        self._stop = asyncio.Event()
        self._consecutive_failures = 0
        self._persistent_alerted = False

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                async with self._bridge.connect() as conn:
                    self._consecutive_failures = 0
                    self._persistent_alerted = False
                    await self._run_session(conn)
            except ImapAuthError as exc:
                # Auth errors don't self-resolve via retry — alert once and
                # wait a long cooldown before trying again (in case the
                # operator rotates credentials).
                log.error("IMAP auth failed: %s", exc)
                await self._fire_persistent_failure()
                await self._sleep_or_stop(self.auth_failure_cooldown)
            except ImapConnectionError as exc:
                self._consecutive_failures += 1
                log.warning(
                    "IMAP session lost (attempt %d): %s",
                    self._consecutive_failures, exc,
                )
                if self._consecutive_failures >= PERSISTENT_FAILURE_THRESHOLD:
                    await self._fire_persistent_failure()
                await self._sleep_or_stop(
                    min(2 ** self._consecutive_failures, 30),
                )
            except Exception:
                log.exception("Unexpected error in IMAP session")
                await self._sleep_or_stop(5)

    async def _fire_persistent_failure(self) -> None:
        if self._persistent_alerted or not self.on_persistent_failure:
            return
        self._persistent_alerted = True
        try:
            await self.on_persistent_failure()
        except Exception:
            log.exception("on_persistent_failure raised")

    async def _sleep_or_stop(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    async def _run_session(self, conn: _BridgeConnection) -> None:
        server_uidnext = await conn.select_inbox()
        checkpoint = await self.db.get_uidnext_checkpoint() or 1
        log.info(
            "IMAP connected to INBOX (server UIDNEXT=%d, checkpoint=%d)",
            server_uidnext, checkpoint,
        )

        for uid in range(checkpoint, server_uidnext):
            await self._process_uid(conn, uid)
        await self.db.set_uidnext_checkpoint(server_uidnext)

        while not self._stop.is_set():
            log.info("Entering IDLE for INBOX")
            await conn.idle_start()
            try:
                saw_exists = await self._wait_for_new_mail(conn)
            finally:
                await conn.idle_done()

            if self._stop.is_set():
                return
            if not saw_exists:
                log.info("IDLE renewed (no new mail in last %ds)", IDLE_RENEW_SECONDS)
                continue
            new_uidnext = await conn.select_inbox()
            current_checkpoint = await self.db.get_uidnext_checkpoint() or 1
            for uid in range(current_checkpoint, new_uidnext):
                await self._process_uid(conn, uid)
            await self.db.set_uidnext_checkpoint(new_uidnext)

    async def _wait_for_new_mail(self, conn: _BridgeConnection) -> bool:
        # Race the IMAP push against the stop signal and the IDLE-renew deadline.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + IDLE_RENEW_SECONDS
        while not self._stop.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            push_task = asyncio.create_task(conn.idle_wait(timeout=remaining))
            stop_task = asyncio.create_task(self._stop.wait())
            try:
                done, _pending = await asyncio.wait(
                    {push_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                if not push_task.done():
                    push_task.cancel()
                if not stop_task.done():
                    stop_task.cancel()
            if stop_task in done:
                return False
            lines = await push_task
            if self._push_signals_new_mail(lines):
                return True
        return False

    @staticmethod
    def _push_signals_new_mail(lines: list[bytes]) -> bool:
        for line in lines:
            payload = line if isinstance(line, (bytes, bytearray)) else b""
            if b"EXISTS" in payload:
                return True
        return False

    async def _process_uid(self, conn: _BridgeConnection, uid: int) -> None:
        try:
            raw = await conn.fetch_raw(uid)
        except ImapConnectionError:
            # Missing UID or transient FETCH failure — the next reconnect
            # will resync against server UIDNEXT and pick it up if it still
            # exists on the server.
            log.debug("FETCH UID %d failed — skipping", uid, exc_info=True)
            return
        try:
            parsed = parse_email(raw)
        except MalformedEmailError:
            # Parser choked on the raw bytes — advancing past the UID is
            # the only way to avoid a tight poison-pill loop.
            log.exception("Unparseable email at UID %d — advancing past it", uid)
            return
        try:
            await self.on_new_email(uid, raw, parsed.headers)
        except Exception:
            # Callback failures must not bring down the listener — the
            # orchestrator owns its own error reporting via the state DB.
            log.exception("on_new_email callback raised for UID %d", uid)
