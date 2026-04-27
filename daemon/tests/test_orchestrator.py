"""Tests for the orchestrator module (email-ingest path)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from audio_ingest.config import DaemonConfig
from audio_ingest.orchestrator import run_daemon, _setup_pipeline_topic_email
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
            patch("audio_ingest.orchestrator.supervise", new=_fake_supervise_run_once),
            patch("audio_ingest.orchestrator.ImapIdleListener") as MockListener,
            patch("audio_ingest.orchestrator.EmailIngestStateDB") as MockDb,
            patch("audio_ingest.orchestrator.TelegramInterface") as MockTii,
            patch("audio_ingest.orchestrator.ThreadStore") as MockThreadStore,
            patch("audio_ingest.orchestrator.SessionManager") as MockSessionMgr,
            patch("audio_ingest.orchestrator.check_topics_enabled", new=AsyncMock(return_value=True)),
            patch("audio_ingest.orchestrator.create_forum_topic", new=AsyncMock(return_value=42)),
            patch("audio_ingest.orchestrator.reopen_forum_topic", new=AsyncMock(return_value=False)),
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
            patch("audio_ingest.orchestrator.supervise", new=fake_supervise),
            patch("audio_ingest.orchestrator.ImapIdleListener") as MockListener,
            patch("audio_ingest.orchestrator.EmailIngestStateDB") as MockDb,
            patch("audio_ingest.orchestrator.TelegramInterface") as MockTii,
            patch("audio_ingest.orchestrator.ThreadStore") as MockThreadStore,
            patch("audio_ingest.orchestrator.SessionManager") as MockSessionMgr,
            patch("audio_ingest.orchestrator.check_topics_enabled", new=AsyncMock(return_value=True)),
            patch("audio_ingest.orchestrator.create_forum_topic", new=AsyncMock(return_value=42)),
            patch("audio_ingest.orchestrator.reopen_forum_topic", new=AsyncMock(return_value=False)),
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

        # Both names must appear, regardless of order
        assert set(supervised_names) == {"imap-listener", "telegram-poller"}, (
            f"unexpected supervised tasks: {supervised_names}"
        )


class TestSetupPipelineTopicEmail:
    @pytest.mark.asyncio
    async def test_reuses_existing_pipeline_topic(self) -> None:
        email_db = AsyncMock()
        email_db.get_setting = AsyncMock(return_value="777")
        bot = BotConfig(bot_token="bot", chat_id="123")

        with (
            patch("audio_ingest.orchestrator.reopen_forum_topic", new_callable=AsyncMock, return_value=True),
            patch("audio_ingest.orchestrator.create_forum_topic", new_callable=AsyncMock) as mock_create,
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
            patch("audio_ingest.orchestrator.reopen_forum_topic", new_callable=AsyncMock, return_value=False),
            patch("audio_ingest.orchestrator.create_forum_topic", new_callable=AsyncMock, return_value=888) as mock_create,
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
            patch("audio_ingest.orchestrator.create_forum_topic", new_callable=AsyncMock, return_value=999),
        ):
            result = await _setup_pipeline_topic_email(email_db, bot)

        assert result == 999

    @pytest.mark.asyncio
    async def test_returns_none_when_topic_creation_fails(self) -> None:
        email_db = AsyncMock()
        email_db.get_setting = AsyncMock(return_value=None)
        bot = BotConfig(bot_token="bot", chat_id="123")

        with (
            patch("audio_ingest.orchestrator.create_forum_topic", new_callable=AsyncMock, side_effect=RuntimeError("fail")),
        ):
            result = await _setup_pipeline_topic_email(email_db, bot)

        assert result is None
