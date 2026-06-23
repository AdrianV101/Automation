"""Forward new-email events to a structured ingest webhook (e.g. Poke).

On each new message this builds a structured JSON record (sender, subject,
body, received_at, message_id) and POSTs it to a configured ingest endpoint.
The endpoint may be self-authenticating (auth token embedded in the URL), in
which case no ``api_key`` is needed; if one is set, a Bearer header is added.
Designed to be composed into an :class:`~email_ingest.listener.ImapIdleListener`'s
``on_new_email`` callback. Network failures are retried with backoff and
ultimately swallowed — a webhook outage must never crash the IDLE listener.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime

import httpx

from .mime import parse_email

log = logging.getLogger(__name__)

DEFAULT_BODY_MAX_CHARS = 4000

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(body: str) -> str:
    # Crude tag strip — good enough for a triage body, no parser dependency.
    text = _TAG_RE.sub(" ", body)
    return html.unescape(text)


def _extract_body(parsed_text: str | None, parsed_html: str | None, limit: int) -> str:
    if parsed_text is not None:
        # Preserve the plain-text body's line structure for readability, but
        # normalize CRLF/CR to LF so the JSON record has clean line endings.
        body = parsed_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    elif parsed_html is not None:
        # HTML layout is lost on strip, so collapse runs of whitespace.
        body = _WS_RE.sub(" ", _strip_html(parsed_html)).strip()
    else:
        body = ""
    if len(body) > limit:
        return body[:limit].rstrip() + "..."
    return body


def _received_at(date_header: str | None) -> str | None:
    """Parse an RFC822 Date header into an ISO-8601 UTC string (``...Z``)."""
    if not date_header:
        return None
    try:
        dt = parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_payload(
    uid: int,
    raw: bytes,
    headers: dict[str, str],
    *,
    body_max_chars: int = DEFAULT_BODY_MAX_CHARS,
) -> dict[str, object]:
    """Build the structured JSON record posted to the ingest endpoint."""
    parsed = parse_email(raw)
    return {
        "sender": headers.get("From", "").strip(),
        "subject": headers.get("Subject", "").strip(),
        "body": _extract_body(parsed.text_body, parsed.html_body, body_max_chars),
        "received_at": _received_at(headers.get("Date")),
        "message_id": parsed.message_id,
    }


@dataclass(frozen=True)
class WebhookConfig:
    url: str
    api_key: str = ""  # optional; Bearer sent only if set (self-auth URLs omit it)
    timeout: float = 10.0
    body_max_chars: int = DEFAULT_BODY_MAX_CHARS
    max_retries: int = 3
    backoff_initial: float = 0.5
    backoff_max: float = 8.0


class WebhookForwarder:
    """POSTs structured new-email records to an ingest endpoint.

    A fresh :class:`httpx.AsyncClient` is created per request — email volume is
    low, so connection reuse buys little and per-request clients keep lifecycle
    management out of the caller. Pass ``transport`` in tests to intercept.
    """

    def __init__(
        self,
        config: WebhookConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport

    async def forward(self, uid: int, raw: bytes, headers: dict[str, str]) -> None:
        """Build the record and POST it, retrying transient failures.

        Matches the ``OnNewEmail`` signature so it can be composed directly
        into a listener callback. Never raises — failures are logged.
        """
        try:
            payload = build_payload(
                uid, raw, headers, body_max_chars=self._config.body_max_chars,
            )
        except Exception:
            log.exception("Failed to build webhook payload for UID %d", uid)
            return
        await self._post_with_retries(payload, uid)

    def _auth_headers(self) -> dict[str, str]:
        if self._config.api_key:
            return {"Authorization": f"Bearer {self._config.api_key}"}
        return {}

    async def _post_with_retries(self, payload: dict[str, object], uid: int) -> None:
        backoff = self._config.backoff_initial
        attempts = self._config.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self._config.timeout, transport=self._transport,
                ) as client:
                    resp = await client.post(
                        self._config.url, json=payload, headers=self._auth_headers(),
                    )
            except httpx.HTTPError as exc:
                # Log the exception type/message but never the URL — it carries
                # the embedded auth token.
                log.warning(
                    "Webhook POST failed for UID %d (attempt %d/%d): %s",
                    uid, attempt, attempts, exc,
                )
            else:
                if resp.status_code < 400:
                    log.debug("Webhook delivered for UID %d (%d)", uid, resp.status_code)
                    return
                if resp.status_code < 500:
                    # Client error — retrying won't help (bad URL, token, payload).
                    log.error(
                        "Webhook rejected UID %d with %d — not retrying",
                        uid, resp.status_code,
                    )
                    return
                log.warning(
                    "Webhook 5xx for UID %d (attempt %d/%d): %d",
                    uid, attempt, attempts, resp.status_code,
                )
            if attempt < attempts:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._config.backoff_max)
        log.error("Webhook delivery gave up for UID %d after %d attempts", uid, attempts)
