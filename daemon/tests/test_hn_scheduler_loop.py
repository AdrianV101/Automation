"""Direct coverage for ``run_scheduler_loop`` (sleep → poll → notify).

The loop is ``while True`` with no exit, so every test patches
``asyncio.sleep`` with a side effect that returns ``None`` for the first N
calls and then raises a sentinel ``_StopLoop``. The sleep call sits *outside*
the loop's ``try/except``, so the sentinel propagates straight out of
``run_scheduler_loop`` and ``_drive_loop`` swallows it — giving precise,
timing-free control over how many full iterations run. ``_StopLoop`` inherits
``BaseException`` (not ``Exception``) so that even if production's
``except Exception`` poll guard ever expanded to cover the sleep, the
sentinel could never be absorbed by the code under test and mis-reported as
a poll-cycle crash — it always terminates the loop cleanly via
``_drive_loop``. No real sleeping ever happens (the AsyncMock never awaits
``asyncio.sleep``).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from audio_ingest.hacker_news_adapter import PollSummary, run_scheduler_loop


class _StopLoop(BaseException):
    """Sentinel raised from the patched ``asyncio.sleep`` to end the loop.

    Inherits ``BaseException``, not ``Exception``, on purpose: production
    wraps ``run_poll_cycle`` in a bare ``except Exception``. An
    ``Exception``-based sentinel that ever reached that guard would be
    caught, logged as a poll crash, and re-raised — quietly contaminating
    the loop tests' meaning. ``BaseException`` bypasses ``except Exception``
    entirely, so the sentinel can only ever do one thing: unwind cleanly to
    ``_drive_loop``'s ``except _StopLoop`` (catch-by-name works for any
    ``BaseException``). It is structurally incapable of being mistaken for a
    poll-cycle failure.
    """


def _sleep_stopping_after(n: int) -> AsyncMock:
    """An ``asyncio.sleep`` replacement: no-op for `n` calls, then ``_StopLoop``.

    With ``n`` no-op sleeps the loop completes exactly ``n`` full
    iterations (each iteration sleeps once at the top before polling), and
    the ``n + 1``-th sleep raises to break out.
    """
    calls = {"count": 0}

    async def _fake_sleep(_secs: float) -> None:
        calls["count"] += 1
        if calls["count"] > n:
            raise _StopLoop
        return None

    return AsyncMock(side_effect=_fake_sleep)


@asynccontextmanager
async def _fake_http_cm():
    yield MagicMock()


def _summary(*, ingested: int = 2, top_title: str = "Some HN story") -> PollSummary:
    return PollSummary(
        ingested=ingested,
        skipped_dedupe=0,
        fetch_failures=0,
        write_failures=0,
        db_failures=0,
        top_title=top_title,
        top_points=321,
    )


async def _drive_loop(**overrides) -> None:
    """Run ``run_scheduler_loop`` with sane test defaults; swallow ``_StopLoop``."""
    kwargs = dict(
        state_db=MagicMock(),
        vault_root=Path("/tmp/vault-unused"),
        telegram_notifier=AsyncMock(),
        news_topic_id=99,
        local_time="05:30",
        tz=ZoneInfo("UTC"),
        min_points=100,
        max_items=25,
        topstories_pool=50,
    )
    kwargs.update(overrides)
    try:
        await run_scheduler_loop(**kwargs)
    except _StopLoop:
        pass


@pytest.mark.asyncio
async def test_full_iteration_polls_then_notifies(tmp_path):
    """AC1/AC6: one full sleep → poll → notify pass, no real sleeping."""
    notifier = AsyncMock()
    fake_poll = AsyncMock(return_value=_summary(ingested=3, top_title="Big news"))
    sleep_mock = _sleep_stopping_after(1)

    with patch(
        "audio_ingest.hacker_news_adapter.asyncio.sleep", new=sleep_mock,
    ), patch(
        "audio_ingest.hacker_news_adapter.httpx.AsyncClient",
        side_effect=lambda *a, **k: _fake_http_cm(),
    ), patch(
        "audio_ingest.hacker_news_adapter.run_poll_cycle", new=fake_poll,
    ):
        await _drive_loop(
            vault_root=tmp_path, telegram_notifier=notifier, news_topic_id=99,
        )

    fake_poll.assert_awaited_once()
    notifier.assert_awaited_once()
    sent_message, sent_kwargs = notifier.await_args.args[0], notifier.await_args.kwargs
    assert "3 stories ingested" in sent_message
    assert "Big news" in sent_message
    assert sent_kwargs["thread_id"] == 99
    # The sentinel-bearing AsyncMock proves no real asyncio.sleep ran.
    assert sleep_mock.await_count == 2


@pytest.mark.asyncio
async def test_poll_cycle_exception_propagates(tmp_path, caplog):
    """AC2: a ``run_poll_cycle`` crash is re-raised so ``supervise()`` restarts."""
    notifier = AsyncMock()
    fake_poll = AsyncMock(side_effect=RuntimeError("HN firebase exploded"))

    with patch(
        "audio_ingest.hacker_news_adapter.asyncio.sleep",
        new=_sleep_stopping_after(5),
    ), patch(
        "audio_ingest.hacker_news_adapter.httpx.AsyncClient",
        side_effect=lambda *a, **k: _fake_http_cm(),
    ), patch(
        "audio_ingest.hacker_news_adapter.run_poll_cycle", new=fake_poll,
    ):
        with pytest.raises(RuntimeError, match="HN firebase exploded"):
            await _drive_loop(vault_root=tmp_path, telegram_notifier=notifier)

    fake_poll.assert_awaited_once()
    notifier.assert_not_awaited()
    # Exactly once: pins that the crash log comes from the real RuntimeError,
    # not from a _StopLoop accidentally caught by production's except Exception
    # (which would also log this line and silently invalidate the test).
    assert caplog.text.count("HN poll cycle crashed; supervisor will restart") == 1


@pytest.mark.asyncio
async def test_telegram_failure_swallowed_loop_continues(tmp_path, caplog):
    """AC3: a Telegram notify failure is logged, not raised; loop keeps going."""
    notifier = AsyncMock(side_effect=RuntimeError("telegram 502"))
    fake_poll = AsyncMock(return_value=_summary())

    with patch(
        "audio_ingest.hacker_news_adapter.asyncio.sleep",
        new=_sleep_stopping_after(2),  # allow two full iterations
    ), patch(
        "audio_ingest.hacker_news_adapter.httpx.AsyncClient",
        side_effect=lambda *a, **k: _fake_http_cm(),
    ), patch(
        "audio_ingest.hacker_news_adapter.run_poll_cycle", new=fake_poll,
    ):
        # Must NOT raise despite the notifier failing every iteration.
        await _drive_loop(vault_root=tmp_path, telegram_notifier=notifier)

    # Loop survived the first Telegram failure and ran a second poll.
    assert fake_poll.await_count == 2
    assert notifier.await_count == 2
    assert "HN Telegram summary notify failed" in caplog.text


@pytest.mark.asyncio
async def test_date_folder_uses_configured_tz_not_utc(tmp_path):
    """AC4: ``date_folder`` reflects the configured tz, not UTC.

    Frozen instant 2026-05-15T23:30Z is still 2026-05-15 in UTC but already
    2026-05-16 in Pacific/Kiritimati (UTC+14). The summary's inbox path must
    use the local date.
    """
    notifier = AsyncMock()
    fake_poll = AsyncMock(return_value=_summary())
    kiritimati = ZoneInfo("Pacific/Kiritimati")

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            base = datetime(2026, 5, 15, 23, 30, tzinfo=timezone.utc)
            return base.astimezone(tz) if tz is not None else base

    with patch(
        "audio_ingest.hacker_news_adapter.asyncio.sleep",
        new=_sleep_stopping_after(1),
    ), patch(
        "audio_ingest.hacker_news_adapter.httpx.AsyncClient",
        side_effect=lambda *a, **k: _fake_http_cm(),
    ), patch(
        "audio_ingest.hacker_news_adapter.run_poll_cycle", new=fake_poll,
    ), patch(
        "audio_ingest.hacker_news_adapter.datetime", _FrozenDateTime,
    ):
        await _drive_loop(
            vault_root=tmp_path, telegram_notifier=notifier, tz=kiritimati,
        )

    sent_message = notifier.await_args.args[0]
    assert "00-Inbox/news/2026-05-16/" in sent_message
    assert "2026-05-15" not in sent_message
