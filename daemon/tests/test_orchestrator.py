"""Tests for the orchestrator module (email-ingest path)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from automation_daemon.config import DaemonConfig
from automation_daemon.orchestrator import run_daemon, _setup_pipeline_topic_email
from telegram_interface import BotConfig


class TestRunDaemonEarlyExits:
    @pytest.mark.asyncio
    async def test_exits_early_when_email_ingest_disabled(self, tmp_path: Path) -> None:
        """run_daemon returns immediately if email ingest is disabled."""
        config = DaemonConfig(
            email_ingest_enabled=False,
            telegram_bot_token="t",
            telegram_chat_id="c",
            pkm_vault_path=tmp_path,
        )
        # Should return without touching any external resources.
        await run_daemon(config)


async def _fake_supervise_run_once(name, factory, **_kwargs):
    """Test helper: run the factory exactly once so TaskGroup completes."""
    try:
        await factory()
    except Exception:
        pass


class TestRunDaemonEmailPath:
    @pytest.mark.asyncio
    async def test_orchestrator_starts_email_listener_when_enabled(
        self, tmp_path: Path,
    ) -> None:
        """run_daemon should start ImapIdleListener when EMAIL_INGEST_ENABLED=true."""
        cfg = DaemonConfig(
            email_ingest_enabled=True,
            imap_host="127.0.0.1",
            imap_port=9999,
            imap_user="u@x",
            imap_password="p",
            telegram_bot_token="t",
            telegram_chat_id="c",
            pkm_vault_path=tmp_path,
            email_ingest_state_db_path=tmp_path / "email.db",
        )

        with (
            patch("automation_daemon.orchestrator.supervise", new=_fake_supervise_run_once),
            patch("automation_daemon.orchestrator.run_speaker_reaper_forever", new=AsyncMock()),
            patch("automation_daemon.orchestrator.ImapIdleListener") as MockListener,
            patch("automation_daemon.orchestrator.EmailIngestStateDB") as MockDb,
            patch("automation_daemon.orchestrator.TelegramInterface") as MockTii,
            patch("automation_daemon.orchestrator.ThreadStore") as MockThreadStore,
            patch("automation_daemon.orchestrator.SessionManager") as MockSessionMgr,
            patch("automation_daemon.orchestrator.check_topics_enabled", new=AsyncMock(return_value=True)),
            patch("automation_daemon.orchestrator.create_forum_topic", new=AsyncMock(return_value=42)),
            patch("automation_daemon.orchestrator.reopen_forum_topic", new=AsyncMock(return_value=False)),
        ):
            mock_listener = MockListener.return_value
            mock_listener.run = AsyncMock(return_value=None)
            mock_db = MockDb.return_value
            mock_db.init_db = AsyncMock(return_value=None)
            mock_db.get_setting = AsyncMock(return_value=None)
            mock_db.set_setting = AsyncMock(return_value=None)
            mock_thread_store = MockThreadStore.return_value
            mock_thread_store.init_db = AsyncMock(return_value=None)
            mock_session_mgr = MockSessionMgr.return_value
            mock_session_mgr.close_all = AsyncMock(return_value=None)
            MockTii.return_value.run_poller = AsyncMock(return_value=None)
            await run_daemon(cfg)
            MockListener.assert_called_once()
            mock_listener.run.assert_awaited()

    @pytest.mark.asyncio
    async def test_orchestrator_wraps_listener_and_poller_in_supervisor(
        self, tmp_path: Path,
    ) -> None:
        """Both long-running tasks must be wrapped in supervise() so a crash
        in one does not bring down the daemon."""
        cfg = DaemonConfig(
            email_ingest_enabled=True,
            imap_host="127.0.0.1", imap_port=9999,
            imap_user="u@x", imap_password="p",
            telegram_bot_token="t", telegram_chat_id="c",
            pkm_vault_path=tmp_path,
            email_ingest_state_db_path=tmp_path / "email.db",
        )

        supervised_names: list[str] = []

        async def fake_supervise(name, factory, **_kwargs):
            # Record the task name and run the factory exactly once so the
            # TaskGroup completes deterministically.
            supervised_names.append(name)
            try:
                await factory()
            except Exception:
                pass

        with (
            patch("automation_daemon.orchestrator.supervise", new=fake_supervise),
            patch("automation_daemon.orchestrator.run_speaker_reaper_forever", new=AsyncMock()),
            patch("automation_daemon.orchestrator.ImapIdleListener") as MockListener,
            patch("automation_daemon.orchestrator.EmailIngestStateDB") as MockDb,
            patch("automation_daemon.orchestrator.TelegramInterface") as MockTii,
            patch("automation_daemon.orchestrator.ThreadStore") as MockThreadStore,
            patch("automation_daemon.orchestrator.SessionManager") as MockSessionMgr,
            patch("automation_daemon.orchestrator.check_topics_enabled", new=AsyncMock(return_value=True)),
            patch("automation_daemon.orchestrator.create_forum_topic", new=AsyncMock(return_value=42)),
            patch("automation_daemon.orchestrator.reopen_forum_topic", new=AsyncMock(return_value=False)),
        ):
            MockListener.return_value.run = AsyncMock(return_value=None)
            mock_db = MockDb.return_value
            mock_db.init_db = AsyncMock(return_value=None)
            mock_db.get_setting = AsyncMock(return_value=None)
            mock_db.set_setting = AsyncMock(return_value=None)
            MockThreadStore.return_value.init_db = AsyncMock(return_value=None)
            MockSessionMgr.return_value.close_all = AsyncMock(return_value=None)
            MockTii.return_value.run_poller = AsyncMock(return_value=None)

            await run_daemon(cfg)

        # All three names must appear, regardless of order
        assert set(supervised_names) == {"imap-listener", "telegram-poller", "speaker-reaper"}, (
            f"unexpected supervised tasks: {supervised_names}"
        )


class TestSetupPipelineTopicEmail:
    @pytest.mark.asyncio
    async def test_reuses_existing_pipeline_topic(self) -> None:
        email_db = AsyncMock()
        email_db.get_setting = AsyncMock(return_value="777")
        bot = BotConfig(bot_token="bot", chat_id="123")

        with (
            patch("automation_daemon.orchestrator.reopen_forum_topic", new_callable=AsyncMock, return_value=True),
            patch("automation_daemon.orchestrator.create_forum_topic", new_callable=AsyncMock) as mock_create,
        ):
            result = await _setup_pipeline_topic_email(email_db, bot)

        assert result == 777
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_new_topic_when_stored_invalid(self) -> None:
        email_db = AsyncMock()
        email_db.get_setting = AsyncMock(return_value="777")
        email_db.set_setting = AsyncMock(return_value=None)
        bot = BotConfig(bot_token="bot", chat_id="123")

        with (
            patch("automation_daemon.orchestrator.reopen_forum_topic", new_callable=AsyncMock, return_value=False),
            patch("automation_daemon.orchestrator.create_forum_topic", new_callable=AsyncMock, return_value=888) as mock_create,
        ):
            result = await _setup_pipeline_topic_email(email_db, bot)

        assert result == 888
        mock_create.assert_called_once_with("Pipeline", bot)
        email_db.set_setting.assert_called_once_with("pipeline_thread_id", "888")

    @pytest.mark.asyncio
    async def test_creates_new_topic_when_no_stored(self) -> None:
        email_db = AsyncMock()
        email_db.get_setting = AsyncMock(return_value=None)
        email_db.set_setting = AsyncMock(return_value=None)
        bot = BotConfig(bot_token="bot", chat_id="123")

        with (
            patch("automation_daemon.orchestrator.create_forum_topic", new_callable=AsyncMock, return_value=999),
        ):
            result = await _setup_pipeline_topic_email(email_db, bot)

        assert result == 999

    @pytest.mark.asyncio
    async def test_returns_none_when_topic_creation_fails(self) -> None:
        email_db = AsyncMock()
        email_db.get_setting = AsyncMock(return_value=None)
        bot = BotConfig(bot_token="bot", chat_id="123")

        with (
            patch("automation_daemon.orchestrator.create_forum_topic", new_callable=AsyncMock, side_effect=RuntimeError("fail")),
        ):
            result = await _setup_pipeline_topic_email(email_db, bot)

        assert result is None


@pytest.mark.asyncio
async def test_run_daemon_starts_news_daily_master_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Smoke test: news_daily_master_enabled creates the scheduler task."""
    from automation_daemon.orchestrator import run_daemon
    from automation_daemon.config import DaemonConfig
    from datetime import time

    cfg = DaemonConfig(
        telegram_bot_token="x", telegram_chat_id="y",
        pkm_vault_path=tmp_path,
        email_ingest_enabled=False,
        news_ingest_enabled=False,
        news_daily_master_enabled=True,
        news_daily_master_local_time="06:00",
        email_ingest_state_db_path=tmp_path / "state.db",
    )

    started = []
    async def fake_run_news_daily(c):
        started.append("news-daily")
        # Return immediately so TaskGroup completes.

    monkeypatch.setattr(
        "automation_daemon.orchestrator._run_news_daily_master_path",
        fake_run_news_daily,
    )
    await run_daemon(cfg)
    assert started == ["news-daily"]


class TestResolveNewsDailyTz:
    """Coverage for the tz fallback chain: configured → localtime → UTC."""

    def test_explicit_iana_zone_used_when_set(self) -> None:
        from automation_daemon.orchestrator import _resolve_news_daily_tz
        from zoneinfo import ZoneInfo
        tz = _resolve_news_daily_tz("Europe/London")
        assert isinstance(tz, ZoneInfo)
        assert str(tz) == "Europe/London"

    def test_invalid_iana_zone_falls_back_with_warning(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        from automation_daemon.orchestrator import _resolve_news_daily_tz
        import logging
        with caplog.at_level(logging.WARNING):
            tz = _resolve_news_daily_tz("Not/A_Real_Zone")
        assert tz is not None
        # Either localtime or UTC, depending on host — but a warning was emitted.
        assert any("Not/A_Real_Zone" in r.message for r in caplog.records)

    def test_empty_string_uses_localtime_or_utc_fallback(self) -> None:
        from automation_daemon.orchestrator import _resolve_news_daily_tz
        tz = _resolve_news_daily_tz("")
        # Don't pin to a specific zone — host-dependent. Just confirm we got
        # something callable.
        assert tz is not None


# ---------------------------------------------------------------------------
# Task E2: gate_or_pass tests
# ---------------------------------------------------------------------------

import pytest
from unittest.mock import AsyncMock, patch

from automation_daemon.config import DaemonConfig
from automation_daemon.models import RecordingJob
from automation_daemon.orchestrator import gate_or_pass
from email_ingest.state import EmailIngestStateDB
from pkm import TranscriptData, TranscriptSegment
from telegram_interface import BotConfig


def _job_gate(speakers: list[str]) -> RecordingJob:
    return RecordingJob(
        id="m1", recorded_at="2026-05-15T10:00:00+00:00", filename="Standup",
        source="plaud-email",
        transcript_data=TranscriptData(
            "m1", "2026-05-15T10:00:00+00:00", 1.0, speakers,
            [TranscriptSegment(0.0, 1.0, sp, f"line {sp}") for sp in speakers],
            "x"),
        duration_ms=1000, source_metadata={},
    )


@pytest.mark.asyncio
async def test_gate_passes_when_all_named(tmp_path) -> None:
    db = EmailIngestStateDB(tmp_path / "s.db"); await db.init_db()
    await db.insert_event("m1", uid=1)
    cfg = DaemonConfig(telegram_bot_token="t", telegram_chat_id="c", pkm_vault_path=tmp_path)
    bot = BotConfig(bot_token="t", chat_id="c")
    gated = await gate_or_pass(_job_gate(["Alice", "Bob"]), cfg, db, bot, thread_id=None)
    assert gated is False
    assert await db.get_pending("m1") is None


@pytest.mark.asyncio
async def test_gate_holds_and_prompts_when_unknown(tmp_path) -> None:
    db = EmailIngestStateDB(tmp_path / "s.db"); await db.init_db()
    await db.insert_event("m1", uid=1)
    cfg = DaemonConfig(telegram_bot_token="t", telegram_chat_id="c", pkm_vault_path=tmp_path)
    bot = BotConfig(bot_token="t", chat_id="c")
    with patch("automation_daemon.orchestrator.send_speaker_prompt",
               new=AsyncMock(side_effect=[101, 102])) as sp:
        gated = await gate_or_pass(_job_gate(["Speaker 1", "Speaker 2"]), cfg, db, bot, thread_id=9)
    assert gated is True
    assert sp.await_count == 2
    row = await db.get_pending("m1")
    assert row["unknown_speakers"] == ["Speaker 1", "Speaker 2"]
    assert row["prompt_message_ids"] == [101, 102]
    assert (await db.get_event("m1"))["status"] == "awaiting_speaker_labels"


# ---------------------------------------------------------------------------
# Task E3: gate wired into _process_parsed_email
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parsed_email_gated_does_not_route(tmp_path) -> None:
    """When the gate holds, process_recording must NOT be called."""
    db = EmailIngestStateDB(tmp_path / "s.db"); await db.init_db()
    cfg = DaemonConfig(telegram_bot_token="t", telegram_chat_id="c", pkm_vault_path=tmp_path)
    bot = BotConfig(bot_token="t", chat_id="c")
    called = AsyncMock()
    # Extra patch: verify_dkim runs before recording_job_from_email on a bare
    # object(); patching it to return a passing result so the test reaches the
    # gate without AttributeError on object().get_all().
    from email_ingest.auth import DkimPass
    with (
        patch("automation_daemon.orchestrator.verify_dkim",
              return_value=DkimPass(signing_domain="example.com", authserv_id="mx")),
        patch("automation_daemon.orchestrator.gate_or_pass",
              new=AsyncMock(return_value=True)),
        patch("automation_daemon.orchestrator.recording_job_from_email",
              return_value=_job_gate(["Speaker 1"])),
    ):
        from automation_daemon.orchestrator import _process_parsed_email
        await _process_parsed_email(
            object(), "m1", email_db=db, config=cfg, bot=bot,
            pipeline_thread=None, process_recording_fn=called,
        )
    called.assert_not_awaited()


# ---------------------------------------------------------------------------
# Task E4: speaker callback + free-text reply handler → resume
# ---------------------------------------------------------------------------

from automation_daemon.speaker_resolution import encode_choice, serialize_job


@pytest.mark.asyncio
async def test_callback_records_name_and_resumes_when_complete(tmp_path) -> None:
    db = EmailIngestStateDB(tmp_path / "s.db"); await db.init_db()
    await db.insert_event("m1", uid=1)
    job = _job_gate(["Speaker 1"])
    await db.insert_pending("m1", payload=serialize_job(job),
                            unknown_speakers=["Speaker 1"])
    await db.set_pending_prompt_ids("m1", [101])
    await db.update_status("m1", "awaiting_speaker_labels")
    cfg = DaemonConfig(telegram_bot_token="t", telegram_chat_id="c", pkm_vault_path=tmp_path)
    bot = BotConfig(bot_token="t", chat_id="c")
    routed = AsyncMock()
    from automation_daemon.orchestrator import make_speaker_callback_handler
    handler = make_speaker_callback_handler(
        email_db=db, config=cfg, bot=bot, process_recording_fn=routed,
    )
    with (
        patch("automation_daemon.orchestrator.answer_callback_query", new=AsyncMock()),
        patch("automation_daemon.orchestrator.send_speaker_resolution_complete", new=AsyncMock()),
    ):
        await handler(callback_query_id="q", message_id=101,
                      data=encode_choice(0, "Alice"))
    routed.assert_awaited_once()
    routed_job = routed.await_args.args[0]
    assert routed_job.transcript_data.speakers == ["Alice"]
    assert await db.get_pending("m1") is None
    assert (await db.get_event("m1"))["status"] in ("writing_pkm", "extracting", "completed")


@pytest.mark.asyncio
async def test_text_reply_records_name_and_resumes_when_complete(tmp_path) -> None:
    db = EmailIngestStateDB(tmp_path / "s.db"); await db.init_db()
    await db.insert_event("m1", uid=1)
    job = _job_gate(["Speaker 1"])
    await db.insert_pending("m1", payload=serialize_job(job),
                            unknown_speakers=["Speaker 1"])
    await db.set_pending_prompt_ids("m1", [101])
    await db.update_status("m1", "awaiting_speaker_labels")
    cfg = DaemonConfig(telegram_bot_token="t", telegram_chat_id="c", pkm_vault_path=tmp_path)
    bot = BotConfig(bot_token="t", chat_id="c")
    routed = AsyncMock()
    from automation_daemon.orchestrator import make_text_reply_handler
    handler = make_text_reply_handler(
        email_db=db, config=cfg, bot=bot, process_recording_fn=routed,
    )
    with patch("automation_daemon.orchestrator.send_speaker_resolution_complete", new=AsyncMock()):
        await handler(reply_to_message_id=101, text="Carol")
    routed.assert_awaited_once()
    routed_job = routed.await_args.args[0]
    assert routed_job.transcript_data.speakers == ["Carol"]
    assert await db.get_pending("m1") is None
    assert (await db.get_event("m1"))["status"] in ("writing_pkm", "extracting", "completed")


# ---------------------------------------------------------------------------
# Task F2: speaker reaper started under supervision
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_email_path_starts_reaper(tmp_path) -> None:
    cfg = DaemonConfig(
        email_ingest_enabled=True, imap_host="h", imap_port=1,
        imap_user="u", imap_password="p", telegram_bot_token="t",
        telegram_chat_id="c", pkm_vault_path=tmp_path,
        email_ingest_state_db_path=tmp_path / "e.db",
    )
    started: list[str] = []

    async def _fake_supervise(name, factory, **kw):
        started.append(name)

    with (
        patch("automation_daemon.orchestrator.supervise", new=_fake_supervise),
        patch("automation_daemon.orchestrator.ImapIdleListener"),
        patch("automation_daemon.orchestrator.EmailIngestStateDB") as Db,
        patch("automation_daemon.orchestrator.TelegramInterface") as MockTii,
        patch("automation_daemon.orchestrator.ThreadStore") as MockThreadStore,
        patch("automation_daemon.orchestrator.SessionManager") as MockSessionMgr,
        patch("automation_daemon.orchestrator.check_topics_enabled", new=AsyncMock(return_value=True)),
        patch("automation_daemon.orchestrator.create_forum_topic", new=AsyncMock(return_value=42)),
        patch("automation_daemon.orchestrator.reopen_forum_topic", new=AsyncMock(return_value=False)),
    ):
        Db.return_value.init_db = AsyncMock()
        Db.return_value.get_setting = AsyncMock(return_value=None)
        Db.return_value.set_setting = AsyncMock(return_value=None)
        MockThreadStore.return_value.init_db = AsyncMock()
        MockSessionMgr.return_value.close_all = AsyncMock()
        MockTii.return_value.run_poller = AsyncMock(return_value=None)
        from automation_daemon.orchestrator import _run_email_ingest_path
        await _run_email_ingest_path(cfg)
    assert any("speaker-reaper" in s for s in started)


# ---------------------------------------------------------------------------
# Task G1: late reply after timeout re-routes with corrected names
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_freetext_reply_targets_speaker_by_prompt_id_with_multiple_unknowns(tmp_path) -> None:
    db = EmailIngestStateDB(tmp_path / "s.db"); await db.init_db()
    await db.insert_event("m1", uid=1)
    job = _job_gate(["Speaker 1", "Speaker 2"])
    await db.insert_pending("m1", payload=serialize_job(job),
                            unknown_speakers=["Speaker 1", "Speaker 2"])
    await db.set_pending_prompt_ids("m1", [201, 202])  # aligned: 201→Speaker 1, 202→Speaker 2
    await db.update_status("m1", "awaiting_speaker_labels")
    cfg = DaemonConfig(telegram_bot_token="t", telegram_chat_id="c", pkm_vault_path=tmp_path)
    bot = BotConfig(bot_token="t", chat_id="c")
    routed = AsyncMock()
    from automation_daemon.orchestrator import make_text_reply_handler
    on_text_reply = make_text_reply_handler(
        email_db=db, config=cfg, bot=bot, process_recording_fn=routed,
    )
    # Operator replies free-text to the SECOND speaker's prompt while BOTH unknown.
    await on_text_reply(reply_to_message_id=202, text="Bob")
    row = await db.get_pending("m1")
    assert row is not None  # not resumed yet — Speaker 1 still unresolved
    assert row["name_map"] == {"Speaker 2": "Bob"}  # correctly targeted, NOT dropped
    routed.assert_not_awaited()
    # Now resolve the first speaker too → resume.
    with patch("automation_daemon.orchestrator.send_speaker_resolution_complete", new=AsyncMock()):
        await on_text_reply(reply_to_message_id=201, text="Alice")
    routed.assert_awaited_once()
    assert routed.await_args.args[0].transcript_data.speakers == ["Alice", "Bob"]
    assert await db.get_pending("m1") is None


@pytest.mark.asyncio
async def test_late_reply_after_timeout_reroutes(tmp_path) -> None:
    db = EmailIngestStateDB(tmp_path / "s.db"); await db.init_db()
    await db.insert_event("old", uid=1)
    job = _job_gate(["Speaker 1"])
    await db.insert_pending("old", payload=serialize_job(job),
                            unknown_speakers=["Speaker 1"])
    await db.set_pending_prompt_ids("old", [101])
    await db.update_status("old", "timed_out_unresolved")  # reaper already ran, row kept
    cfg = DaemonConfig(telegram_bot_token="t", telegram_chat_id="c", pkm_vault_path=tmp_path)
    bot = BotConfig(bot_token="t", chat_id="c")
    routed = AsyncMock()
    from automation_daemon.orchestrator import make_speaker_callback_handler
    handler = make_speaker_callback_handler(
        email_db=db, config=cfg, bot=bot, process_recording_fn=routed,
    )
    with (
        patch("automation_daemon.orchestrator.answer_callback_query", new=AsyncMock()),
        patch("automation_daemon.orchestrator.send_speaker_resolution_complete", new=AsyncMock()),
    ):
        await handler(callback_query_id="q", message_id=101,
                      data=encode_choice(0, "Alice"))
    routed.assert_awaited_once()
    assert routed.await_args.args[0].transcript_data.speakers == ["Alice"]
