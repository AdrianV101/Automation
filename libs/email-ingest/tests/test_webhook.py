from __future__ import annotations

import json

import httpx

from email_ingest.webhook import WebhookConfig, WebhookForwarder, build_payload

RAW_TEXT = (
    b"Message-ID: <abc123@proton.me>\r\n"
    b"From: Alice Example <alice@example.com>\r\n"
    b"Subject: Quarterly report\r\n"
    b"Date: Tue, 23 Jun 2026 21:11:59 +0000\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Hello Bob,\r\n\r\nThis is the body of the message.\r\n"
)

RAW_HTML_ONLY = (
    b"Message-ID: <html1@proton.me>\r\n"
    b"From: news@example.com\r\n"
    b"Subject: HTML only\r\n"
    b"Date: Tue, 23 Jun 2026 09:00:00 +0100\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n"
    b"\r\n"
    b"<html><body><p>Click&nbsp;<b>here</b> now</p></body></html>\r\n"
)

INGEST_URL = "https://poke.com/api/v1/inbound/ingest/token123"


def _headers(raw: bytes) -> dict[str, str]:
    # Mirror what the listener passes to on_new_email (parsed.headers).
    from email_ingest import parse_email

    return parse_email(raw).headers


def test_build_payload_extracts_structured_fields() -> None:
    payload = build_payload(42, RAW_TEXT, _headers(RAW_TEXT))
    assert payload["sender"] == "Alice Example <alice@example.com>"
    assert payload["subject"] == "Quarterly report"
    assert payload["body"] == "Hello Bob,\n\nThis is the body of the message."
    assert payload["received_at"] == "2026-06-23T21:11:59Z"
    assert payload["message_id"] == "abc123@proton.me"


def test_build_payload_normalizes_received_at_to_utc() -> None:
    # +0100 must be converted to UTC with a trailing Z.
    payload = build_payload(1, RAW_HTML_ONLY, _headers(RAW_HTML_ONLY))
    assert payload["received_at"] == "2026-06-23T08:00:00Z"


def test_build_payload_received_at_none_when_no_date() -> None:
    raw = b"Message-ID: <e@x>\r\nFrom: a@x\r\nSubject: s\r\n\r\nbody"
    payload = build_payload(1, raw, _headers(raw))
    assert payload["received_at"] is None


def test_build_payload_falls_back_to_html_body() -> None:
    payload = build_payload(7, RAW_HTML_ONLY, _headers(RAW_HTML_ONLY))
    assert payload["body"] == "Click here now"


def test_build_payload_truncates_long_body() -> None:
    long_body = b"word " * 2000
    raw = (
        b"Message-ID: <l@x>\r\nFrom: a@x\r\nSubject: s\r\n"
        b"Content-Type: text/plain\r\n\r\n" + long_body
    )
    payload = build_payload(1, raw, _headers(raw), body_max_chars=100)
    assert len(payload["body"]) <= 103  # 100 + "..."
    assert payload["body"].endswith("...")


def test_build_payload_handles_missing_headers() -> None:
    raw = b"Message-ID: <e@x>\r\n\r\n"
    payload = build_payload(3, raw, _headers(raw))
    assert payload["sender"] == ""
    assert payload["subject"] == ""
    assert payload["body"] == ""


async def test_forward_posts_structured_json_without_auth_header() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["content_type"] = request.headers.get("content-type")
        seen["body"] = request.content
        return httpx.Response(200, json={"status": "ok"})

    fwd = WebhookForwarder(
        WebhookConfig(url=INGEST_URL),  # self-authenticating URL, no api_key
        transport=httpx.MockTransport(handler),
    )
    await fwd.forward(99, RAW_TEXT, _headers(RAW_TEXT))

    assert seen["url"] == INGEST_URL
    # Self-authenticating URL → no Authorization header.
    assert seen["auth"] is None
    assert "application/json" in seen["content_type"]
    body = json.loads(seen["body"])
    assert body["sender"] == "Alice Example <alice@example.com>"
    assert body["subject"] == "Quarterly report"
    assert body["received_at"] == "2026-06-23T21:11:59Z"
    assert "body" in body


async def test_forward_sends_bearer_when_api_key_provided() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200)

    fwd = WebhookForwarder(
        WebhookConfig(url=INGEST_URL, api_key="k"),
        transport=httpx.MockTransport(handler),
    )
    await fwd.forward(1, RAW_TEXT, _headers(RAW_TEXT))
    assert seen["auth"] == "Bearer k"


async def test_forward_retries_on_5xx_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    fwd = WebhookForwarder(
        WebhookConfig(url=INGEST_URL, max_retries=3, backoff_initial=0.0),
        transport=httpx.MockTransport(handler),
    )
    await fwd.forward(1, RAW_TEXT, _headers(RAW_TEXT))
    assert calls["n"] == 3


async def test_forward_gives_up_after_retries_without_raising() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    fwd = WebhookForwarder(
        WebhookConfig(url=INGEST_URL, max_retries=2, backoff_initial=0.0),
        transport=httpx.MockTransport(handler),
    )
    # Must not raise — webhook failures cannot crash the IDLE listener.
    await fwd.forward(1, RAW_TEXT, _headers(RAW_TEXT))
    assert calls["n"] == 3  # initial try + 2 retries


async def test_forward_does_not_retry_on_4xx() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401)

    fwd = WebhookForwarder(
        WebhookConfig(url=INGEST_URL, max_retries=3, backoff_initial=0.0),
        transport=httpx.MockTransport(handler),
    )
    await fwd.forward(1, RAW_TEXT, _headers(RAW_TEXT))
    assert calls["n"] == 1  # no retries on client error


async def test_forward_swallows_network_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    fwd = WebhookForwarder(
        WebhookConfig(url=INGEST_URL, max_retries=1, backoff_initial=0.0),
        transport=httpx.MockTransport(handler),
    )
    # Network failure must be swallowed, not propagated.
    await fwd.forward(1, RAW_TEXT, _headers(RAW_TEXT))
