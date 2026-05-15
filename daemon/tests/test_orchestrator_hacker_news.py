from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from automation_daemon.config import DaemonConfig


def _cfg(tmp_path, *, hn_enabled: bool):
    return DaemonConfig(
        telegram_bot_token="tok",
        telegram_chat_id="chat",
        pkm_vault_path=tmp_path,
        email_ingest_enabled=False,
        news_ingest_enabled=False,
        email_ingest_state_db_path=tmp_path / "state.db",
        imap_user="u",
        imap_password="p",
        hacker_news_enabled=hn_enabled,
        news_telegram_topic_id=99,
    )


@pytest.mark.asyncio
async def test_run_daemon_starts_hn_task_when_enabled(tmp_path):
    cfg = _cfg(tmp_path, hn_enabled=True)
    started = asyncio.Event()

    async def fake_loop(*args, **kwargs):
        started.set()
        await asyncio.sleep(3600)

    async def fake_supervise(name, factory, **kw):
        await factory()

    with patch(
        "automation_daemon.orchestrator.run_scheduler_loop",
        side_effect=fake_loop,
    ), patch("automation_daemon.orchestrator.supervise", side_effect=fake_supervise):
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
async def test_run_daemon_skips_hn_task_when_disabled(tmp_path):
    cfg = _cfg(tmp_path, hn_enabled=False)
    cfg = DaemonConfig(
        **{**cfg.__dict__, "email_ingest_enabled": True},  # one path enabled
    )
    started = asyncio.Event()

    async def fake_loop(*args, **kwargs):
        started.set()
        await asyncio.sleep(3600)

    async def fake_email_path(*a, **k):
        await asyncio.sleep(3600)

    with patch(
        "automation_daemon.orchestrator.run_scheduler_loop",
        side_effect=fake_loop,
    ), patch(
        "automation_daemon.orchestrator._run_email_ingest_path",
        side_effect=fake_email_path,
    ):
        from automation_daemon.orchestrator import run_daemon
        task = asyncio.create_task(run_daemon(cfg))
        try:
            # Give the loop a moment; HN task must NOT have started.
            await asyncio.sleep(0.2)
            assert not started.is_set()
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, BaseExceptionGroup):
                pass


@pytest.mark.asyncio
async def test_run_daemon_no_paths_enabled_exits(tmp_path, caplog):
    """Sanity: HN-only enabled is a valid single-path deployment."""
    cfg = _cfg(tmp_path, hn_enabled=False)  # nothing enabled
    from automation_daemon.orchestrator import run_daemon
    await run_daemon(cfg)
    assert "No ingestion path enabled" in caplog.text
