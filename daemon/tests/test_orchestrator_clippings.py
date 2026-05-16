from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from automation_daemon.config import DaemonConfig


def _cfg(tmp_path, *, clippings_enabled: bool, email_enabled: bool = False):
    return DaemonConfig(
        telegram_bot_token="tok",
        telegram_chat_id="chat",
        pkm_vault_path=tmp_path,
        email_ingest_enabled=email_enabled,
        news_ingest_enabled=False,
        email_ingest_state_db_path=tmp_path / "state.db",
        imap_user="u",
        imap_password="p",
        clippings_enabled=clippings_enabled,
        news_telegram_topic_id=99,
    )


@pytest.mark.asyncio
async def test_run_daemon_starts_clippings_task_when_enabled(tmp_path):
    cfg = _cfg(tmp_path, clippings_enabled=True)
    started = asyncio.Event()

    async def fake_loop(**kwargs):
        started.set()
        await asyncio.sleep(3600)

    async def fake_supervise(name, factory, **kw):
        await factory()

    with patch("automation_daemon.orchestrator.run_clippings_watch_loop",
               side_effect=fake_loop), \
         patch("automation_daemon.orchestrator.supervise", side_effect=fake_supervise):
        from automation_daemon.orchestrator import run_daemon
        task = asyncio.create_task(run_daemon(cfg))
        try:
            await asyncio.wait_for(started.wait(), timeout=2.0)
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, BaseExceptionGroup):
                pass


@pytest.mark.asyncio
async def test_run_daemon_skips_clippings_when_disabled(tmp_path):
    cfg = _cfg(tmp_path, clippings_enabled=False, email_enabled=True)
    with patch("automation_daemon.orchestrator.run_clippings_watch_loop") as loop_mock, \
         patch("automation_daemon.orchestrator._run_email_ingest_path",
               new=AsyncMock()), \
         patch("automation_daemon.orchestrator.supervise", new=AsyncMock()):
        from automation_daemon.orchestrator import run_daemon
        await asyncio.wait_for(run_daemon(cfg), timeout=2.0)
    loop_mock.assert_not_called()
