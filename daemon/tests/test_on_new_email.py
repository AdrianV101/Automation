from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from automation_daemon.orchestrator import make_on_new_email

_PLAUD_RAW = (
    b"From: Plaud AutoFlow <no-reply@plaud.ai>\r\n"
    b"To: transcripts@example.com\r\n"
    b"Subject: [Plaud AutoFlow] Weekly standup\r\n"
    b"Message-ID: <autoflow-1@plaud.ai>\r\n"
    b"Authentication-Results: mail.protonmail.ch; dkim=pass header.d=plaud.ai\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Speaker 1 00:00:01\r\nHello.\r\n"
)
_HEADERS = {"Subject": "[Plaud AutoFlow] Weekly standup", "From": "no-reply@plaud.ai"}


@pytest.mark.asyncio
async def test_forwards_when_configured() -> None:
    forwarder = MagicMock()
    forwarder.forward = AsyncMock()
    on_new_email = make_on_new_email(forwarder)
    await on_new_email(7, _PLAUD_RAW, _HEADERS)
    forwarder.forward.assert_awaited_once_with(7, _PLAUD_RAW, _HEADERS)


@pytest.mark.asyncio
async def test_noop_without_forwarder() -> None:
    on_new_email = make_on_new_email(None)
    await on_new_email(7, b"Subject: hi\r\n\r\nbody", {"Subject": "hi"})


@pytest.mark.asyncio
async def test_plaud_email_only_forwarded() -> None:
    import automation_daemon.orchestrator as orchestrator

    assert not hasattr(orchestrator, "process_recording")
    assert not hasattr(orchestrator, "handle_incoming_email")
    forwarder = MagicMock()
    forwarder.forward = AsyncMock()
    on_new_email = make_on_new_email(forwarder)
    await on_new_email(8, _PLAUD_RAW, _HEADERS)
    forwarder.forward.assert_awaited_once_with(8, _PLAUD_RAW, _HEADERS)
