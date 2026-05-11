from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol
from zoneinfo import ZoneInfo

from email_ingest import (
    BridgeConfig,
    EmailIngestStateDB,
    EmailIngestStatusTracker,
    ImapIdleListener,
    NewsIngestStateDB,
    parse_email,
    verify_dkim,
)

from .config import DaemonConfig
from .command_config import build_daemon_commands
from .hacker_news_adapter import run_scheduler_loop
from .hn_state import HackerNewsStateDB
from .supervisor import supervise
from .models import RecordingJob, StatusTracker
from .notifications import format_file_list
from .news_email_adapter import handle_news_email
from .pipeline import process_recording
from .plaud_email_adapter import MalformedPlaudEmailError, recording_job_from_email
from agent_infra import SessionManager
from telegram_interface import (
    BotConfig, TelegramInterface, ThreadStore,
    check_topics_enabled, create_forum_topic, reopen_forum_topic, send_message,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Telegram callback_query routing
# ---------------------------------------------------------------------------

# Hardcoded rather than imported from news_personal_digest.render to keep this
# dispatcher loadable when the digest feature is disabled (and to keep
# dispatch_callback_query itself free of digest-module imports).
_DIGEST_CALLBACK_PREFIX = "nr"

CallbackQueryHandler = Callable[..., Awaitable[None]]


async def dispatch_callback_query(
    *,
    callback_query_id: str,
    message_id: int,
    data: str,
    digest_handler: CallbackQueryHandler | None,
    fallback_handler: CallbackQueryHandler | None = None,
) -> None:
    """Route a Telegram callback_query by data prefix.

    `nr:` → digest rating handler. Anything else → fallback (e.g. speaker
    labeling). With no digest_handler, even nr: routes to fallback. With
    no fallback, unrecognised data is dropped silently — no toast, no DB
    write — because the bot is shared across features and we don't want
    one feature to claim ownership of unfamiliar callbacks.
    """
    if digest_handler is not None and data.startswith(f"{_DIGEST_CALLBACK_PREFIX}:"):
        await digest_handler(
            callback_query_id=callback_query_id,
            message_id=message_id,
            data=data,
        )
        return
    if fallback_handler is not None:
        await fallback_handler(
            callback_query_id=callback_query_id,
            message_id=message_id,
            data=data,
        )


class ProcessRecording(Protocol):
    async def __call__(
        self,
        job: RecordingJob,
        config: DaemonConfig,
        *,
        status: StatusTracker | None = None,
    ) -> None: ...


async def run_daemon(config: DaemonConfig) -> None:
    log.info("Daemon started")

    if not (
        config.email_ingest_enabled
        or config.news_ingest_enabled
        or config.news_daily_master_enabled
        or config.hacker_news_enabled
    ):
        log.error(
            "No ingestion path enabled. Set EMAIL_INGEST_ENABLED=true and/or "
            "NEWS_INGEST_ENABLED=true and/or NEWS_DAILY_MASTER_ENABLED=true "
            "and/or HACKER_NEWS_ENABLED=true. Exiting.",
        )
        return

    async with asyncio.TaskGroup() as tg:
        if config.email_ingest_enabled:
            tg.create_task(_run_email_ingest_path(config), name="email-ingest")
        if config.news_ingest_enabled:
            tg.create_task(_run_news_ingest_path(config), name="news-ingest")
        if config.news_daily_master_enabled:
            tg.create_task(
                _run_news_daily_master_path(config),
                name="news-daily-master",
            )
        if config.hacker_news_enabled:
            tg.create_task(_run_hacker_news_path(config), name="hacker-news")


async def handle_incoming_email(
    uid: int,
    raw: bytes,
    *,
    email_db: EmailIngestStateDB,
    config: DaemonConfig,
    bot: BotConfig,
    pipeline_thread: int | None,
    process_recording_fn: ProcessRecording = process_recording,
) -> None:
    parsed = parse_email(raw)
    message_id = parsed.message_id

    if not message_id:
        log.warning("Email at UID %d has no Message-ID — skipping", uid)
        return

    if await email_db.is_processed(message_id):
        log.debug("Already processed %s — skipping", message_id)
        return

    # Insert the event row before any processing so unexpected failures
    # (DKIM edge cases, DB errors, programmer bugs) leave a durable trace
    # instead of silently losing the UID.
    await email_db.insert_event(
        message_id=message_id, uid=uid,
        subject=parsed.headers.get("Subject"),
    )

    try:
        await _process_parsed_email(
            parsed, message_id,
            email_db=email_db, config=config, bot=bot,
            pipeline_thread=pipeline_thread,
            process_recording_fn=process_recording_fn,
        )
    except Exception as exc:
        # Catch-all so the row never gets stuck in 'received'. Narrow-typed
        # branches (DKIM fail, MalformedPlaudEmailError) are handled inside
        # _process_parsed_email; anything that reaches here is unexpected.
        log.exception("Unexpected error handling email %s", message_id)
        try:
            await email_db.update_status(
                message_id, "failed", error=f"unexpected: {exc}",
            )
        except Exception:
            log.exception("Failed to mark %s failed after crash", message_id)


async def _process_parsed_email(
    parsed,
    message_id: str,
    *,
    email_db: EmailIngestStateDB,
    config: DaemonConfig,
    bot: BotConfig,
    pipeline_thread: int | None,
    process_recording_fn: ProcessRecording,
) -> None:
    dkim = verify_dkim(
        parsed,
        required_domain=config.dkim_required_domain,
        trusted_authserv_id=config.dkim_trusted_authserv_id,
    )
    if not dkim.passed:
        log.warning(
            "DKIM fail for %s (domain=%s, reason=%s) — rejecting",
            message_id, dkim.signing_domain, dkim.reason,
        )
        await email_db.update_status(
            message_id, "failed", error=f"dkim_fail: {dkim.reason}",
        )
        await _notify_pipeline(
            bot, pipeline_thread,
            "⚠️ DKIM verification failed\n\n"
            f"Subject: {parsed.headers.get('Subject', '')}\n"
            f"Message-ID: {message_id}\n"
            f"Signing domain: {dkim.signing_domain}\n"
            f"Reason: {dkim.reason}",
            message_id,
        )
        return

    try:
        job = recording_job_from_email(
            parsed,
            vault_path=config.pkm_vault_path,
            attachments_subdir=config.vault_attachments_subdir,
        )
    except MalformedPlaudEmailError as exc:
        log.error("Malformed Plaud email %s: %s", message_id, exc)
        await email_db.update_status(message_id, "failed", error=str(exc))
        await _notify_pipeline(
            bot, pipeline_thread,
            "⚠️ Plaud email parse failed\n\n"
            f"Subject: {parsed.headers.get('Subject', '')}\n"
            f"Message-ID: {message_id}\nError: {exc}",
            message_id,
        )
        return

    if job is None:
        log.debug("Not-for-us email %s — dropping", message_id)
        await email_db.update_status(message_id, "dropped", error="not-for-us")
        return

    tracker = EmailIngestStatusTracker(email_db, message_id)
    await process_recording_fn(job, config, status=tracker)


async def _notify_pipeline(
    bot: BotConfig, pipeline_thread: int | None, text: str, message_id: str,
) -> None:
    try:
        await send_message(text, bot, thread_id=pipeline_thread)
    except Exception:
        log.exception("Failed to send Telegram error for %s", message_id)


async def _run_email_ingest_path(config: DaemonConfig) -> None:
    email_db = EmailIngestStateDB(config.email_ingest_state_db_path)
    await email_db.init_db()
    thread_store = ThreadStore(config.email_ingest_state_db_path)
    await thread_store.init_db()
    session_mgr = SessionManager(
        session_store=thread_store, pkm_vault_path=config.pkm_vault_path,
    )

    bot = BotConfig(
        bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id,
    )

    tii = TelegramInterface(
        bot_config=bot,
        commands=build_daemon_commands(config.pkm_vault_path),
        session_manager=session_mgr,
        thread_store=thread_store,
        status_provider=None,  # no status backend for email ingest yet
        file_formatter=format_file_list,
        agent_inactivity_timeout_s=config.agent_inactivity_timeout_s,
    )

    topics_ok = await check_topics_enabled(bot)
    if not topics_ok:
        log.warning(
            "Telegram topics not enabled on chat %s.",
            config.telegram_chat_id,
        )

    pipeline_thread = await _setup_pipeline_topic_email(email_db, bot)

    async def on_new_email(uid: int, raw: bytes, headers: dict[str, str]) -> None:
        await handle_incoming_email(
            uid, raw,
            email_db=email_db, config=config, bot=bot,
            pipeline_thread=pipeline_thread,
        )

    async def on_persistent_failure() -> None:
        await send_message(
            "⚠️ Email Bridge Disconnected\n\n"
            "Unable to connect to IMAP bridge for 3 consecutive attempts.\n"
            "Will keep retrying. Check Proton Mail Bridge status on the VPS.",
            bot, thread_id=pipeline_thread,
        )

    bridge_cfg = BridgeConfig(
        host=config.imap_host, port=config.imap_port,
        user=config.imap_user, password=config.imap_password,
        use_starttls=config.imap_use_starttls,
        ssl_verify=config.imap_ssl_verify,
    )
    listener = ImapIdleListener(
        bridge_cfg, email_db,
        on_new_email=on_new_email,
        on_persistent_failure=on_persistent_failure,
    )

    async def on_supervised_task_crashloop(task_name: str, failures: int) -> None:
        try:
            await send_message(
                f"⚠️ Daemon task crash-looping\n\n"
                f"Task: {task_name}\n"
                f"Consecutive failures: {failures}\n"
                f"Will keep restarting with backoff. Check daemon logs.",
                bot, thread_id=pipeline_thread,
            )
        except Exception:
            log.exception("Failed to send crash-loop alert for %s", task_name)

    # Wire digest rating callbacks only when the digest feature is enabled.
    digest_handler = await _build_digest_callback_handler(config, bot)

    async def on_callback_query(cbq_id: str, msg_id: int, data: str) -> None:
        await dispatch_callback_query(
            callback_query_id=cbq_id, message_id=msg_id, data=data,
            digest_handler=digest_handler,
            fallback_handler=None,  # speaker-labeling routes via on_labeling_reply
        )

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(supervise(
                "imap-listener", listener.run,
                on_persistent_failure=on_supervised_task_crashloop,
            ))
            tg.create_task(supervise(
                "telegram-poller",
                lambda: tii.run_poller(
                    max_concurrent_dispatch=config.max_concurrent_dispatch,
                    on_callback_query=on_callback_query if digest_handler else None,
                ),
                on_persistent_failure=on_supervised_task_crashloop,
            ))
    finally:
        await session_mgr.close_all()


async def _build_digest_callback_handler(
    config: DaemonConfig, bot: BotConfig,
) -> CallbackQueryHandler | None:
    """Build the digest rating-callback handler, or None when disabled."""
    if not config.news_personal_digest_enabled:
        return None
    from .news_personal_digest.callback import (
        DigestCallbackDeps, handle_rating_callback,
    )
    from .news_personal_digest.state import NewsDigestStateDB
    from .notifications import (
        answer_callback_query as _raw_answer,
        edit_message_reply_markup as _raw_edit,
    )

    digest_db = NewsDigestStateDB(config.email_ingest_state_db_path)
    await digest_db.init_db()

    async def edit_reply_markup_kw(
        *, message_id: int, reply_markup: dict,
    ) -> None:
        await _raw_edit(
            chat_id=config.telegram_chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
            tg=bot,
        )

    async def answer_cbq_kw(
        *, callback_query_id: str, text: str,
    ) -> None:
        await _raw_answer(callback_query_id, text, bot)

    deps = DigestCallbackDeps(
        db=digest_db,
        edit_message_reply_markup=edit_reply_markup_kw,
        answer_callback_query=answer_cbq_kw,
    )

    async def handler(
        *, callback_query_id: str, message_id: int, data: str,
    ) -> None:
        await handle_rating_callback(
            callback_query_id=callback_query_id,
            message_id=message_id, data=data, deps=deps,
        )

    return handler


async def _setup_pipeline_topic_email(
    email_db: EmailIngestStateDB, bot: BotConfig,
) -> int | None:
    pipeline_thread: int | None = None
    stored_thread = await email_db.get_setting("pipeline_thread_id")
    if stored_thread:
        try:
            tid = int(stored_thread)
        except (ValueError, TypeError):
            log.warning("Stored pipeline_thread_id %r is not valid, ignoring", stored_thread)
            tid = None
        if tid is not None and await reopen_forum_topic(tid, bot):
            pipeline_thread = tid
            log.info("Reusing existing Pipeline topic (thread_id=%d)", tid)
        elif tid is not None:
            log.warning("Stored Pipeline topic %d no longer valid, creating new one", tid)
    if pipeline_thread is None:
        try:
            pipeline_thread = await create_forum_topic("Pipeline", bot)
            if pipeline_thread is not None:
                await email_db.set_setting("pipeline_thread_id", str(pipeline_thread))
                log.info("Created new Pipeline topic (thread_id=%d)", pipeline_thread)
        except Exception:
            log.exception("Failed to create Pipeline forum topic, using general channel")
    return pipeline_thread


async def _run_news_ingest_path(config: DaemonConfig) -> None:
    db = NewsIngestStateDB(config.email_ingest_state_db_path)
    await db.init_db()

    bot = BotConfig(
        bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id,
    )

    async def telegram_notifier(text: str, *, thread_id: int | None = None) -> None:
        await send_message(text, bot, thread_id=thread_id)

    async def on_new_email(uid: int, raw: bytes, headers: dict[str, str]) -> None:
        await handle_news_email(
            uid, raw,
            db=db,
            vault_root=config.pkm_vault_path,
            telegram_notifier=telegram_notifier,
            news_topic_id=config.news_telegram_topic_id,
        )

    async def on_persistent_failure() -> None:
        try:
            await send_message(
                "⚠️ News Bridge Disconnected\n\n"
                "Unable to connect to IMAP bridge for the News folder for "
                "3 consecutive attempts. Will keep retrying.",
                bot, thread_id=config.news_telegram_topic_id,
            )
        except Exception:
            log.exception("Failed to send news persistent-failure alert")

    async def on_supervised_crashloop(task_name: str, failures: int) -> None:
        try:
            await send_message(
                f"⚠️ Daemon task crash-looping\n\n"
                f"Task: {task_name}\nConsecutive failures: {failures}\n"
                f"Will keep restarting with backoff. Check daemon logs.",
                bot, thread_id=config.news_telegram_topic_id,
            )
        except Exception:
            log.exception("Failed to send news crash-loop alert")

    bridge_cfg = BridgeConfig(
        host=config.imap_host, port=config.imap_port,
        user=config.news_imap_user or config.imap_user,
        password=config.imap_password,
        use_starttls=config.imap_use_starttls,
        ssl_verify=config.imap_ssl_verify,
    )
    listener = ImapIdleListener(
        cfg=bridge_cfg, db=db,
        on_new_email=on_new_email,
        on_persistent_failure=on_persistent_failure,
        folder=config.news_imap_folder,
    )
    await supervise(
        "news-imap-listener", listener.run,
        on_persistent_failure=on_supervised_crashloop,
    )


async def _run_news_daily_master_path(config: DaemonConfig) -> None:
    from datetime import time as _time
    from .news_daily_master.runner import (
        RunnerConfig, run_for_date, run_agent_via_agent_infra,
    )
    from .news_daily_master.scheduler import NewsDailyScheduler
    from .news_daily_master.state import NewsDailyMasterStateDB

    db = NewsDailyMasterStateDB(config.email_ingest_state_db_path)
    await db.init_db()

    bot = BotConfig(
        bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id,
    )

    async def notify(text: str) -> None:
        try:
            await send_message(
                text, bot, thread_id=config.news_daily_telegram_topic_id,
            )
        except Exception:
            log.exception("Failed to send news daily master notification")

    runner_cfg = RunnerConfig(
        vault_root=config.pkm_vault_path,
        model=config.news_daily_master_model,
        telegram_topic_id=config.news_daily_telegram_topic_id,
    )

    # Chain the personalised digest after each successful master run when
    # news_personal_digest_enabled; otherwise the master path runs alone.
    digest_db = None
    digest_runner_cfg = None
    digest_run_for_date_fn = None
    if config.news_personal_digest_enabled:
        from .news_personal_digest.runner import (
            DigestRunnerConfig,
            run_for_date as _digest_run_for_date,
            run_agent_via_agent_infra as _digest_run_agent,
        )
        from .news_personal_digest.state import NewsDigestStateDB
        from telegram_interface import send_message_return_id

        digest_db = NewsDigestStateDB(config.email_ingest_state_db_path)
        await digest_db.init_db()
        digest_runner_cfg = DigestRunnerConfig(
            vault_root=config.pkm_vault_path,
            model=config.news_personal_digest_model,
            feedback_window_days=config.news_personal_digest_feedback_window_days,
            min_items=config.news_personal_digest_min_items,
            max_items=config.news_personal_digest_max_items,
            telegram_topic_id=config.news_daily_telegram_topic_id,
        )

        async def _send_digest_messages(
            messages: list[tuple[str, dict]],
        ) -> list[int | None]:
            # Position-preserving: None at index i means "message i failed to
            # send". The runner relies on len(result) == len(messages) to keep
            # item ↔ message_id attachment correctly aligned across categories.
            ids: list[int | None] = []
            for text, kb in messages:
                try:
                    msg_id = await send_message_return_id(
                        text, bot,
                        thread_id=config.news_daily_telegram_topic_id,
                        reply_markup=kb,
                    )
                except Exception:
                    log.exception("Failed to send digest message")
                    ids.append(None)
                    continue
                if msg_id is None:
                    # Telegram returned ok=true but no message_id in result
                    # — a Bot API contract violation worth flagging
                    # distinctly from the exception branch above.
                    log.warning(
                        "send_message_return_id returned None for digest "
                        "message (Telegram ok=true but no message_id)"
                    )
                    ids.append(None)
                else:
                    ids.append(int(msg_id))
            return ids

        async def digest_run_for_date_fn(d) -> None:
            await _digest_run_for_date(
                d, db=digest_db, config=digest_runner_cfg,
                run_agent=_digest_run_agent,
                notify=notify, send_messages=_send_digest_messages,
            )

    async def run_for_date_fn(d) -> None:
        await run_for_date(
            d, db=db, config=runner_cfg,
            run_agent=run_agent_via_agent_infra,
            notify=notify,
        )
        # Chain digest only when master completed cleanly.
        if digest_run_for_date_fn is None:
            return
        master_row = await db.get_run(d)
        if master_row is None or master_row.get("status") != "completed":
            log.info(
                "Skipping digest for %s — master status=%s",
                d, master_row.get("status") if master_row else None,
            )
            return
        try:
            await digest_run_for_date_fn(d)
        except Exception:
            log.exception("Digest run failed for %s", d)

    h, m = config.news_daily_master_local_time.split(":", 1)
    fire = _time(int(h), int(m))
    tz = _resolve_news_daily_tz(config.news_daily_master_tz)

    sched = NewsDailyScheduler(
        db=db, run_for_date_fn=run_for_date_fn, notify=notify,
        fire_time=fire, tz=tz,
        backfill_window_days=config.news_daily_master_backfill_days,
    )
    await sched.run_forever()


async def _run_hacker_news_path(config: DaemonConfig) -> None:
    state_db = HackerNewsStateDB(config.email_ingest_state_db_path)
    await state_db.init_db()

    bot = BotConfig(
        bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id,
    )

    async def telegram_notifier(text: str, *, thread_id: int | None = None) -> None:
        await send_message(text, bot, thread_id=thread_id)

    async def on_persistent_failure(task_name: str, failures: int) -> None:
        try:
            await send_message(
                f"⚠️ Hacker News scheduler crash-looping\n\n"
                f"Task: {task_name}\nConsecutive failures: {failures}\n"
                f"Will keep restarting with backoff. Check daemon logs.",
                bot, thread_id=config.news_telegram_topic_id,
            )
        except Exception:
            log.exception("Failed to send HN crash-loop alert")

    # Reuse NEWS_DAILY_MASTER_TZ to keep the timezone config surface minimal;
    # the same fallback chain (configured -> localtime -> UTC) is used.
    tz = _resolve_news_daily_tz(config.news_daily_master_tz)

    async def factory() -> None:
        await run_scheduler_loop(
            state_db=state_db,
            vault_root=config.pkm_vault_path,
            telegram_notifier=telegram_notifier,
            news_topic_id=config.news_telegram_topic_id,
            local_time=config.hacker_news_local_time,
            tz=tz,
            min_points=config.hacker_news_min_points,
            max_items=config.hacker_news_max_items,
            topstories_pool=config.hacker_news_topstories_pool,
        )

    await supervise(
        "hacker-news-scheduler", factory,
        on_persistent_failure=on_persistent_failure,
    )


def _resolve_news_daily_tz(configured_tz: str):
    """Resolve the timezone for the news daily scheduler.

    Order of preference: explicit `NEWS_DAILY_MASTER_TZ` (e.g. "Europe/London"),
    then `localtime` (works on macOS/Linux where /etc/localtime is set), then
    UTC as a last-resort fallback. Each fallback step is logged at WARNING so
    operators see in startup logs why the scheduler may not be firing at the
    wall-clock hour they expect.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if configured_tz:
        try:
            return ZoneInfo(configured_tz)
        except ZoneInfoNotFoundError:
            log.warning(
                "NEWS_DAILY_MASTER_TZ=%r is not a valid IANA zone; "
                "falling back to system localtime",
                configured_tz,
            )
    try:
        return ZoneInfo("localtime")
    except ZoneInfoNotFoundError:
        log.warning(
            "Could not resolve system localtime via ZoneInfo (no tzdata?); "
            "falling back to UTC. Set NEWS_DAILY_MASTER_TZ to override.",
        )
        return ZoneInfo("UTC")
