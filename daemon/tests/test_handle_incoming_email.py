"""Behavioral coverage for the orchestrator's on_new_email callback.

Exercises the extracted `handle_incoming_email` function with a real state
DB and stubbed side effects for each branch: success, DKIM fail, malformed,
not-for-us, duplicate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from audio_ingest.config import DaemonConfig
from audio_ingest.orchestrator import handle_incoming_email
from email_ingest import EmailIngestStateDB
from telegram_interface import BotConfig

FIXTURES = Path(__file__).parent / "fixtures" / "emails"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
async def email_db(tmp_path: Path) -> EmailIngestStateDB:
    db = EmailIngestStateDB(tmp_path / "state.db")
    await db.init_db()
    return db


@pytest.fixture
def config(tmp_path: Path) -> DaemonConfig:
    return DaemonConfig(
        telegram_bot_token="t",
        telegram_chat_id="c",
        pkm_vault_path=tmp_path / "vault",
        dkim_trusted_authserv_id="mail.protonmail.ch",
        dkim_required_domain="plaud.ai",
        vault_attachments_subdir="99-Attachments/plaud",
    )


@pytest.fixture
def bot() -> BotConfig:
    return BotConfig(bot_token="t", chat_id="c")


@pytest.fixture
def process_recording_spy() -> AsyncMock:
    return AsyncMock()


async def test_valid_email_reaches_process_recording(
    email_db: EmailIngestStateDB,
    config: DaemonConfig,
    bot: BotConfig,
    process_recording_spy: AsyncMock,
) -> None:
    raw = _load("plaud_real_01.eml")
    with patch("audio_ingest.orchestrator.send_message", new_callable=AsyncMock):
        await handle_incoming_email(
            uid=1, raw=raw,
            email_db=email_db, config=config, bot=bot, pipeline_thread=42,
            process_recording_fn=process_recording_spy,
        )
    process_recording_spy.assert_awaited_once()
    job, cfg = process_recording_spy.call_args[0][0], process_recording_spy.call_args[0][1]
    assert job.source == "plaud-email"
    assert cfg is config


async def test_dkim_failure_persists_row_and_alerts(
    email_db: EmailIngestStateDB,
    config: DaemonConfig,
    bot: BotConfig,
    process_recording_spy: AsyncMock,
) -> None:
    raw = _load("synth_dkim_fail.eml")
    with patch("audio_ingest.orchestrator.send_message", new_callable=AsyncMock) as send:
        await handle_incoming_email(
            uid=2, raw=raw,
            email_db=email_db, config=config, bot=bot, pipeline_thread=42,
            process_recording_fn=process_recording_spy,
        )
    process_recording_spy.assert_not_awaited()
    send.assert_awaited_once()
    assert "DKIM verification failed" in send.call_args[0][0]
    row = await email_db.get_event(_message_id_from(raw))
    assert row is not None
    assert row["status"] == "failed"
    assert "dkim_fail" in row["error"]


async def test_forged_authentication_results_rejected(
    email_db: EmailIngestStateDB,
    config: DaemonConfig,
    bot: BotConfig,
    process_recording_spy: AsyncMock,
) -> None:
    # Attacker-injected AR header from untrusted MTA must not bypass verification.
    forged_raw = (
        b"From: <no-reply@plaud.ai>\r\n"
        b"To: <adrian@example.com>\r\n"
        b"Subject: [Plaud-AutoFlow] hi\r\n"
        b"Message-ID: <forged-123@attacker.example>\r\n"
        b"Authentication-Results: attacker.example; dkim=pass d=plaud.ai\r\n"
        b"\r\n"
        b"body\r\n"
    )
    with patch("audio_ingest.orchestrator.send_message", new_callable=AsyncMock):
        await handle_incoming_email(
            uid=3, raw=forged_raw,
            email_db=email_db, config=config, bot=bot, pipeline_thread=42,
            process_recording_fn=process_recording_spy,
        )
    process_recording_spy.assert_not_awaited()
    row = await email_db.get_event("forged-123@attacker.example")
    assert row["status"] == "failed"
    assert "trusted authserv-id" in row["error"]


async def test_malformed_email_rejected_and_alert_sent(
    email_db: EmailIngestStateDB,
    config: DaemonConfig,
    bot: BotConfig,
    process_recording_spy: AsyncMock,
) -> None:
    raw = _load("synth_no_transcript.eml")
    with patch("audio_ingest.orchestrator.send_message", new_callable=AsyncMock) as send:
        await handle_incoming_email(
            uid=4, raw=raw,
            email_db=email_db, config=config, bot=bot, pipeline_thread=42,
            process_recording_fn=process_recording_spy,
        )
    process_recording_spy.assert_not_awaited()
    send.assert_awaited_once()
    assert "parse failed" in send.call_args[0][0]
    row = await email_db.get_event(_message_id_from(raw))
    assert row["status"] == "failed"
    assert "transcript.txt" in row["error"]


async def test_not_for_us_email_marked_dropped(
    email_db: EmailIngestStateDB,
    config: DaemonConfig,
    bot: BotConfig,
    process_recording_spy: AsyncMock,
) -> None:
    raw = _load("synth_wrong_from.eml")
    with patch("audio_ingest.orchestrator.send_message", new_callable=AsyncMock):
        await handle_incoming_email(
            uid=5, raw=raw,
            email_db=email_db, config=config, bot=bot, pipeline_thread=42,
            process_recording_fn=process_recording_spy,
        )
    process_recording_spy.assert_not_awaited()
    row = await email_db.get_event(_message_id_from(raw))
    assert row["status"] == "dropped"


async def test_duplicate_message_id_skipped(
    email_db: EmailIngestStateDB,
    config: DaemonConfig,
    bot: BotConfig,
    process_recording_spy: AsyncMock,
) -> None:
    raw = _load("plaud_real_01.eml")
    with patch("audio_ingest.orchestrator.send_message", new_callable=AsyncMock):
        await handle_incoming_email(
            uid=6, raw=raw,
            email_db=email_db, config=config, bot=bot, pipeline_thread=42,
            process_recording_fn=process_recording_spy,
        )
        await handle_incoming_email(
            uid=6, raw=raw,
            email_db=email_db, config=config, bot=bot, pipeline_thread=42,
            process_recording_fn=process_recording_spy,
        )
    process_recording_spy.assert_awaited_once()


async def test_process_recording_exception_marks_row_failed(
    email_db: EmailIngestStateDB,
    config: DaemonConfig,
    bot: BotConfig,
) -> None:
    raw = _load("plaud_real_01.eml")
    boom = AsyncMock(side_effect=RuntimeError("extraction blew up"))
    with patch("audio_ingest.orchestrator.send_message", new_callable=AsyncMock):
        await handle_incoming_email(
            uid=10, raw=raw,
            email_db=email_db, config=config, bot=bot, pipeline_thread=42,
            process_recording_fn=boom,
        )
    row = await email_db.get_event(_message_id_from(raw))
    assert row is not None
    assert row["status"] == "failed"
    assert "extraction blew up" in row["error"]


async def test_empty_message_id_skipped(
    email_db: EmailIngestStateDB,
    config: DaemonConfig,
    bot: BotConfig,
    process_recording_spy: AsyncMock,
) -> None:
    raw_no_id = (
        b"From: <no-reply@plaud.ai>\r\n"
        b"To: <a@example.com>\r\n"
        b"Subject: [Plaud-AutoFlow] hi\r\n"
        b"\r\n"
        b"body\r\n"
    )
    await handle_incoming_email(
        uid=7, raw=raw_no_id,
        email_db=email_db, config=config, bot=bot, pipeline_thread=42,
        process_recording_fn=process_recording_spy,
    )
    process_recording_spy.assert_not_awaited()


def _message_id_from(raw: bytes) -> str:
    from email_ingest import parse_email
    return parse_email(raw).message_id
