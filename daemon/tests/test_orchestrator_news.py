"""Verify run_daemon starts the news listener when only news is enabled."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from automation_daemon.config import DaemonConfig


@pytest.mark.asyncio
async def test_run_daemon_starts_news_listener_when_enabled(tmp_path):
    cfg = DaemonConfig(
        telegram_bot_token="tok",
        telegram_chat_id="chat",
        pkm_vault_path=tmp_path,
        email_ingest_enabled=False,
        news_ingest_enabled=True,
        news_imap_folder="News",
        news_telegram_topic_id=99,
        email_ingest_state_db_path=tmp_path / "state.db",
        imap_user="u",
        imap_password="p",
    )

    started = asyncio.Event()
    captured_folder: list[str] = []

    async def fake_run(self):
        captured_folder.append(self.folder)
        started.set()
        await asyncio.sleep(3600)

    # Patch listener.run to avoid real Bridge connection; patch supervise to
    # call the factory once (no restart loop) so the test exits cleanly when
    # we cancel; patch check_topics_enabled to avoid real Telegram API call.
    async def fake_supervise(name, factory, **kw):
        await factory()

    with patch("automation_daemon.orchestrator.ImapIdleListener.run", fake_run), \
         patch("automation_daemon.orchestrator.check_topics_enabled", AsyncMock(return_value=True)), \
         patch("automation_daemon.orchestrator.supervise", side_effect=fake_supervise):
        from automation_daemon.orchestrator import run_daemon
        task = asyncio.create_task(run_daemon(cfg))
        try:
            await asyncio.wait_for(started.wait(), timeout=2.0)
            assert captured_folder == ["News"]
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, BaseExceptionGroup):
                pass


@pytest.mark.asyncio
async def test_run_daemon_exits_when_no_path_enabled(tmp_path, caplog):
    cfg = DaemonConfig(
        telegram_bot_token="tok",
        telegram_chat_id="chat",
        pkm_vault_path=tmp_path,
        email_ingest_enabled=False,
        news_ingest_enabled=False,
    )
    from automation_daemon.orchestrator import run_daemon
    await run_daemon(cfg)  # should return cleanly without trying to connect


@pytest.mark.asyncio
async def test_run_daemon_starts_both_paths_when_both_enabled(tmp_path):
    """The actual production config: both ingestion paths active. Guards
    against a regression dropping one of the two tg.create_task lines."""
    cfg = DaemonConfig(
        telegram_bot_token="tok",
        telegram_chat_id="chat",
        pkm_vault_path=tmp_path,
        email_ingest_enabled=True,
        news_ingest_enabled=True,
        news_imap_folder="News",
        news_telegram_topic_id=99,
        email_ingest_state_db_path=tmp_path / "state.db",
        imap_user="u",
        imap_password="p",
    )

    started_paths: list[str] = []

    async def fake_email_path(_config):
        started_paths.append("email")
        await asyncio.sleep(3600)

    async def fake_news_path(_config):
        started_paths.append("news")
        await asyncio.sleep(3600)

    with patch("automation_daemon.orchestrator._run_email_ingest_path", fake_email_path), \
         patch("automation_daemon.orchestrator._run_news_ingest_path", fake_news_path):
        from automation_daemon.orchestrator import run_daemon
        task = asyncio.create_task(run_daemon(cfg))
        try:
            for _ in range(50):
                if {"email", "news"} <= set(started_paths):
                    break
                await asyncio.sleep(0.02)
            assert "email" in started_paths
            assert "news" in started_paths
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, BaseExceptionGroup):
                pass
