from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import ssl
from dataclasses import dataclass
from typing import AsyncIterator

import aioimaplib
from aioimaplib.aioimaplib import Command

from .errors import ImapAuthError, ImapConnectionError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BridgeConfig:
    host: str
    port: int
    user: str
    password: str
    timeout: float = 30.0
    backoff_initial: float = 1.0
    backoff_max: float = 30.0
    backoff_multiplier: float = 2.0
    # Proton Mail Bridge on localhost serves a self-signed cert and requires
    # STARTTLS — hence the loopback-scoped ssl_verify=False default at the
    # daemon level. ssl_verify is only meaningful when use_starttls is True.
    use_starttls: bool = False
    ssl_verify: bool = True

    def __post_init__(self) -> None:
        if not (1 <= self.port <= 65535):
            raise ValueError(f"port must be 1-65535, got {self.port}")
        if self.timeout <= 0 or self.backoff_initial <= 0 or self.backoff_max <= 0:
            raise ValueError("timeout and backoff values must be positive")
        if self.backoff_multiplier < 1:
            raise ValueError(f"backoff_multiplier must be >= 1, got {self.backoff_multiplier}")
        # ssl_verify only makes sense when TLS is engaged. A plaintext config
        # with ssl_verify=False looks like "TLS disabled" but is actually
        # "TLS never happened" — reject the ambiguity.
        if not self.use_starttls and not self.ssl_verify:
            raise ValueError(
                "ssl_verify=False is only meaningful with use_starttls=True",
            )


async def _do_starttls(
    client: aioimaplib.IMAP4, server_hostname: str, verify: bool,
) -> None:
    protocol = client.protocol
    cmd = Command("STARTTLS", protocol.new_tag())
    response = await protocol.execute(cmd)
    if response.result != "OK":
        raise ImapConnectionError(f"STARTTLS failed: {response}")

    ssl_ctx = ssl.create_default_context()
    if not verify:
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    loop = asyncio.get_running_loop()
    new_transport = await loop.start_tls(
        protocol.transport, protocol, ssl_ctx,
        server_hostname=server_hostname,
    )
    protocol.transport = new_transport

    # Server capabilities can change after STARTTLS (e.g. new AUTH types),
    # so refresh them before any downstream `has_capability` checks.
    protocol.capabilities = set()
    await protocol.capability()


class _BridgeConnection:
    def __init__(self, client: aioimaplib.IMAP4) -> None:
        self._client = client
        self._uidnext: int | None = None

    async def select_inbox(self) -> int:
        result = await self._client.select("INBOX")
        if result.result != "OK":
            raise ImapConnectionError(f"SELECT failed: {result}")
        for line in result.lines:
            text = line.decode() if isinstance(line, (bytes, bytearray)) else line
            if "UIDNEXT" in text:
                try:
                    start = text.index("UIDNEXT") + len("UIDNEXT")
                    token = text[start:].lstrip(" [").split()[0]
                    token = token.rstrip("]")
                    self._uidnext = int(token)
                except (ValueError, IndexError):
                    continue
        if self._uidnext is None:
            raise ImapConnectionError(f"could not parse UIDNEXT from {result.lines}")
        return self._uidnext

    async def fetch_raw(self, uid: int) -> bytes:
        # aioimaplib returns FETCH results as a list like:
        #   [b'1 FETCH (UID N RFC822 {SIZE}', bytearray(<body>), b')', b'FETCH completed']
        # Prefer the bytearray entry when present; fall back to the first
        # non-status bytes line for servers that inline the body.
        result = await self._client.uid("fetch", str(uid), "(RFC822)")
        if result.result != "OK":
            raise ImapConnectionError(f"FETCH {uid} failed: {result}")
        for line in result.lines:
            if isinstance(line, bytearray):
                return bytes(line)
        for line in result.lines:
            if not isinstance(line, bytes):
                continue
            upper = line.upper()
            if b"FETCH" in upper or line.strip() in (b")", b""):
                continue
            return line
        raise ImapConnectionError(f"no body found in FETCH result: {result.lines}")

    async def mark_seen(self, uid: int) -> None:
        await self._client.uid("store", str(uid), "+FLAGS", "(\\Seen)")

    async def idle_start(self) -> None:
        await self._client.idle_start()

    async def idle_wait(self, timeout: float) -> list[bytes]:
        # aioimaplib's `wait_server_push` return type has varied across
        # versions (bytes, str, list, bytearray); normalise to list[bytes].
        try:
            result = await asyncio.wait_for(
                self._client.wait_server_push(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            return []
        if isinstance(result, list):
            return [x if isinstance(x, bytes) else str(x).encode() for x in result]
        if isinstance(result, bytes):
            return [result]
        if isinstance(result, bytearray):
            return [bytes(result)]
        if isinstance(result, str):
            return [result.encode()]
        return []

    async def idle_done(self) -> None:
        self._client.idle_done()


class ImapBridge:
    def __init__(self, cfg: BridgeConfig) -> None:
        self._cfg = cfg

    async def _establish(self) -> aioimaplib.IMAP4:
        delay = self._cfg.backoff_initial
        last_exc: Exception | None = None
        for attempt in range(5):
            client: aioimaplib.IMAP4 | None = None
            try:
                client = aioimaplib.IMAP4(
                    host=self._cfg.host, port=self._cfg.port, timeout=self._cfg.timeout,
                )
                await client.wait_hello_from_server()
                if self._cfg.use_starttls:
                    await _do_starttls(
                        client,
                        server_hostname=self._cfg.host,
                        verify=self._cfg.ssl_verify,
                    )
                result = await client.login(self._cfg.user, self._cfg.password)
                if result.result != "OK":
                    # LOGIN NO/BAD is not transient — surface immediately so
                    # the listener can page the operator instead of looping.
                    raise ImapAuthError(f"LOGIN failed: {result}")
                return client
            except ImapAuthError:
                _close_client(client)
                raise
            except Exception as exc:
                _close_client(client)
                last_exc = exc
                log.warning(
                    "IMAP connect attempt %d failed: %s", attempt + 1, exc,
                )
                sleep_for = min(delay, self._cfg.backoff_max)
                jitter = random.uniform(0, sleep_for * 0.1)
                await asyncio.sleep(sleep_for + jitter)
                delay *= self._cfg.backoff_multiplier
        raise ImapConnectionError(f"IMAP bridge unreachable after 5 attempts: {last_exc}")

    @contextlib.asynccontextmanager
    async def connect(self) -> AsyncIterator[_BridgeConnection]:
        client = await self._establish()
        try:
            yield _BridgeConnection(client)
        finally:
            log.info("IMAP disconnected from INBOX")
            try:
                await client.logout()
            except Exception:
                log.debug("IMAP logout raised during cleanup", exc_info=True)
            _close_client(client)


def _close_client(client: aioimaplib.IMAP4 | None) -> None:
    if client is None:
        return
    try:
        transport = client.protocol.transport
        if transport is not None:
            transport.close()
    except Exception:
        log.debug("IMAP transport close raised", exc_info=True)
