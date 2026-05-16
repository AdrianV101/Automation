from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

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
from .clippings_adapter import run_clippings_watch_loop
from .clippings_state import ClippingsStateDB
from .hacker_news_adapter import run_scheduler_loop
from .hn_state import HackerNewsStateDB
from .supervisor import supervise
from .models import RecordingJob, StatusTracker
from .notifications import (
    answer_callback_query, format_file_list, send_speaker_prompt,
    send_speaker_resolution_complete,
)
from .people import load_people
from .speaker_resolution import (
    apply_speaker_names, decode_choice, deserialize_job,
    IGNORE, is_unrecognised, OTHER, serialize_job, unrecognised_speakers,
)
from .news_email_adapter import handle_news_email
from .pipeline import process_recording
from .plaud_email_adapter import MalformedPlaudEmailError, recording_job_from_email
from .speaker_reaper import run_speaker_reaper_forever
from agent_infra import SessionManager
from telegram_interface import (
    BotConfig, TelegramInterface, ThreadStore,
    check_topics_enabled, create_forum_topic, reopen_forum_topic, send_message,
    send_message_return_id,
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

    `nr:` → digest rating handler. Anything else → fallback_handler. In the
    daemon the fallback is a composed handler: speaker-labelling is always
    wired, and when clippings is enabled `clip:`-prefixed callbacks are
    routed to the clippings clarification handler (the composed fallback
    tries clippings first, then speaker labelling). With no digest_handler,
    even nr: routes to fallback. With no fallback, unrecognised data is
    dropped silently — the bot is shared across features and no feature
    should claim ownership of unfamiliar callbacks.
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
        speakers_unresolved: bool = False,
    ) -> None: ...


async def run_daemon(config: DaemonConfig) -> None:
    log.info("Daemon started")

    if not (
        config.email_ingest_enabled
        or config.news_ingest_enabled
        or config.news_daily_master_enabled
        or config.news_weekly_enabled
        or config.hacker_news_enabled
        or config.clippings_enabled
    ):
        log.error(
            "No ingestion path enabled. Set EMAIL_INGEST_ENABLED=true and/or "
            "NEWS_INGEST_ENABLED=true and/or NEWS_DAILY_MASTER_ENABLED=true "
            "and/or NEWS_WEEKLY_ENABLED=true and/or HACKER_NEWS_ENABLED=true. "
            "Exiting.",
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
        if config.clippings_enabled:
            tg.create_task(_run_clippings_path(config), name="clippings")
        if config.news_weekly_enabled:
            tg.create_task(
                _run_news_weekly_patterns_path(config),
                name="news-weekly-patterns",
            )


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


def _speaker_snippet(job: RecordingJob, label: str) -> str:
    for seg in job.transcript_data.segments:
        if seg.speaker == label and seg.text.strip():
            return seg.text
    return "(no sample available)"


async def gate_or_pass(
    job: RecordingJob,
    config: DaemonConfig,
    email_db: EmailIngestStateDB,
    bot: BotConfig,
    *,
    thread_id: int | None,
) -> bool:
    """If the transcript has unrecognised speakers, persist a pending
    record, send one Telegram prompt per unknown speaker, mark the event
    `awaiting_speaker_labels`, and return True (caller must NOT route).
    Otherwise return False (caller routes normally)."""
    unknown = unrecognised_speakers(job.transcript_data)
    if not unknown:
        return False
    await email_db.insert_pending(
        job.id, payload=serialize_job(job), unknown_speakers=unknown,
    )
    known = [p.name for p in load_people(config.pkm_vault_path)]
    prompt_ids: list[int | None] = []
    for idx, label in enumerate(unknown):
        try:
            mid = await send_speaker_prompt(
                speaker_idx=idx, speaker_label=label,
                recording_name=job.filename,
                sample_text=_speaker_snippet(job, label),
                tg=bot, known_names=known, thread_id=thread_id,
            )
        except Exception:
            log.exception(
                "Failed to send speaker prompt for %s (speaker %d/%d %r); "
                "slot preserved as None for positional alignment",
                job.id, idx + 1, len(unknown), label,
            )
            mid = None
        # Keep prompt_ids positionally aligned with `unknown`: index i is
        # always speaker i, so a free-text reply can be mapped back by the
        # replied-to message id's position even with multiple unknowns.
        prompt_ids.append(mid)
    await email_db.set_pending_prompt_ids(job.id, prompt_ids)
    await email_db.update_status(job.id, "awaiting_speaker_labels")
    if all(mid is None for mid in prompt_ids):
        # Every prompt failed — operator would have no way to label the
        # recording. Send an explicit notice so the failure is not silent.
        try:
            await send_message(
                "⚠️ Held a recording with unrecognised speakers but couldn't "
                "deliver the labelling prompt(s) — will retry/await; check "
                "Telegram connectivity.",
                bot, thread_id=thread_id,
            )
        except Exception:
            log.exception("Failed to send prompt-delivery-failure notice for %s", job.id)
    return True


async def _find_pending_by_prompt_id(
    email_db: EmailIngestStateDB, message_id: int,
) -> dict | None:
    # Delegate to the DB method which is poison-resilient and uses a direct
    # query instead of the old sentinel-date full-table scan.
    return await email_db.get_pending_by_prompt_id(message_id)


async def _maybe_resume(
    msg_id: str, email_db: EmailIngestStateDB, config: DaemonConfig,
    bot: BotConfig, process_recording_fn,
) -> None:
    row = await email_db.get_pending(msg_id)
    if row is None:
        return
    if set(row["name_map"]) != set(row["unknown_speakers"]):
        return  # still waiting on at least one speaker

    # Guard against a corrupt stored payload — treat as poison: log, mark
    # failed, drop the pending row, notify operator, and return without routing.
    try:
        job = deserialize_job(row["payload"])
    except Exception:
        log.exception(
            "speaker resume: stored payload for %s is unreadable; dropping", msg_id,
        )
        try:
            await email_db.update_status(msg_id, "failed")
        except Exception:
            log.exception("speaker resume: failed to mark %s failed after bad payload", msg_id)
        await email_db.delete_pending(msg_id)
        try:
            await send_message(
                "⚠️ A held recording's stored data was unreadable; dropped — "
                "re-send if needed.",
                bot,
            )
        except Exception:
            log.exception("speaker resume: failed to send notice for poisoned payload %s", msg_id)
        return

    job = replace(
        job,
        transcript_data=apply_speaker_names(job.transcript_data, row["name_map"]),
    )
    # Any speaker the operator chose IGNORE for keeps its generic label as the
    # mapped name — flag these for the pipeline so it knows resolution is partial.
    speakers_unresolved = any(
        is_unrecognised(k) and v == k
        for k, v in row["name_map"].items()
    )

    # Route FIRST; only clean up (delete row + send completion notice) on success.
    # This preserves the pending row for retry/reaper if routing fails.
    tracker = EmailIngestStatusTracker(email_db, msg_id)
    try:
        await process_recording_fn(
            job, config, status=tracker, speakers_unresolved=speakers_unresolved,
        )
    except Exception:
        log.exception(
            "speaker resume: routing failed for %s; pending row left for retry/reaper",
            msg_id,
        )
        raise  # surface to the handler wrapper so the operator is notified

    await send_speaker_resolution_complete(job.filename, row["name_map"], bot)
    await email_db.delete_pending(msg_id)


def make_speaker_callback_handler(
    *,
    email_db: EmailIngestStateDB,
    config: DaemonConfig,
    bot: BotConfig,
    process_recording_fn=process_recording,
):
    async def handler(*, callback_query_id: str, message_id: int, data: str) -> None:
        try:
            decoded = decode_choice(data)
            if decoded is None:
                return  # not ours; dispatch_callback_query already filtered, be safe
            speaker_idx, choice = decoded
            row = await _find_pending_by_prompt_id(email_db, message_id)
            if row is None:
                await answer_callback_query(callback_query_id, "No longer pending", bot)
                return
            msg_id = row["message_id"]
            unknown = row["unknown_speakers"]
            if speaker_idx >= len(unknown):
                await answer_callback_query(callback_query_id, "Unknown speaker", bot)
                return
            label = unknown[speaker_idx]
            if choice == OTHER:
                # Ask for free text; the reply handler completes the mapping.
                await answer_callback_query(
                    callback_query_id, f"Reply with the name for {label}", bot,
                )
                return
            name = label if choice == IGNORE else choice
            await email_db.set_pending_name(msg_id, label, name)
            await answer_callback_query(callback_query_id, f"{label} → {name}", bot)
            await _maybe_resume(msg_id, email_db, config, bot, process_recording_fn)
        except Exception:
            log.exception("speaker callback handler failed for query %s", callback_query_id)
            try:
                await answer_callback_query(
                    callback_query_id,
                    "Couldn't record that — try again or check logs",
                    bot,
                )
            except Exception:
                log.exception(
                    "Failed to send error toast for callback query %s", callback_query_id,
                )
    return handler


def make_text_reply_handler(
    *,
    email_db: EmailIngestStateDB,
    config: DaemonConfig,
    bot: BotConfig,
    process_recording_fn=process_recording,
):
    async def on_text_reply(reply_to_message_id: int, text: str) -> None:
        row = await _find_pending_by_prompt_id(email_db, reply_to_message_id)
        if row is None:
            return  # not a speaker-prompt reply (silent — could be unrelated)
        prompt_ids = row["prompt_message_ids"]
        try:
            idx = prompt_ids.index(reply_to_message_id)
        except ValueError:
            # Matched a pending row but the reply id isn't in its prompt list —
            # stale or misrouted reply; tell the operator.
            try:
                await send_message(
                    "⚠️ Couldn't map that reply to a speaker — the prompt may "
                    "be stale; re-trigger labelling.",
                    bot,
                )
            except Exception:
                log.exception("Failed to send unmappable-reply notice")
            return
        unknown = row["unknown_speakers"]
        if idx >= len(unknown):
            # Positional alignment broken (defensive/legacy row); tell the operator.
            try:
                await send_message(
                    "⚠️ Couldn't map that reply to a speaker — the prompt may "
                    "be stale; re-trigger labelling.",
                    bot,
                )
            except Exception:
                log.exception("Failed to send unmappable-reply notice")
            return
        label = unknown[idx]
        await email_db.set_pending_name(row["message_id"], label, text.strip())
        await _maybe_resume(
            row["message_id"], email_db, config, bot, process_recording_fn,
        )
    return on_text_reply


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

    if await gate_or_pass(
        job, config, email_db, bot, thread_id=pipeline_thread,
    ):
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

    # Wire clippings clarification callbacks when the clippings feature is enabled.
    # When disabled, clip_callback and clip_text_reply remain None, so the
    # composed fallback/text-reply handlers below behave exactly like the
    # speaker-labelling-only path (no clippings handlers registered).
    clip_callback = None
    clip_text_reply = None
    if config.clippings_enabled:
        from .clippings_telegram import (
            handle_clip_callback, handle_clip_text_reply, make_finalize_route,
        )
        from .notifications import answer_callback_query as _raw_answer

        clip_state = ClippingsStateDB(config.email_ingest_state_db_path)
        await clip_state.init_db()

        async def _clip_notifier(text: str, *, thread_id: int | None = None) -> None:
            await send_message(text, bot, thread_id=thread_id)

        _clip_finalize = make_finalize_route(
            clippings_dir=config.pkm_vault_path / config.clippings_dir,
            vault_root=config.pkm_vault_path,
            state=clip_state,
            telegram_notifier=_clip_notifier,
            list_routing_targets=lambda: _clippings_routing_targets(config.pkm_vault_path),
            news_topic_id=config.news_telegram_topic_id,
            model=config.clippings_model,
        )

        async def _clip_answer(*, callback_query_id: str, text: str) -> None:
            await _raw_answer(callback_query_id, text, bot)

        async def clip_callback(  # type: ignore[assignment]
            *, callback_query_id: str, message_id: int, data: str,
        ) -> None:
            await handle_clip_callback(
                callback_query_id=callback_query_id, message_id=message_id,
                data=data, state=clip_state, finalize_route=_clip_finalize,
                answer_callback_query=_clip_answer,
            )

        async def clip_text_reply(  # type: ignore[assignment]
            reply_to_message_id: int, text: str,
        ) -> bool:
            """Return True if a pending clarification consumed this reply.

            Returning the consumed signal lets the composed text-reply
            handler fall through to speaker labelling when this reply was
            not for a clipping clarification.
            """
            async def _rerun(*, url_key: str, user_guidance: str) -> None:
                await _clip_finalize(url_key=url_key, user_guidance=user_guidance)
            return await handle_clip_text_reply(
                text=text, reply_to_message_id=reply_to_message_id,
                state=clip_state, rerun_with_guidance=_rerun,
            )

    speaker_cb = make_speaker_callback_handler(
        email_db=email_db, config=config, bot=bot,
    )

    async def _composed_fallback(
        *, callback_query_id: str, message_id: int, data: str,
    ) -> None:
        # Clippings owns `clip:`-prefixed callbacks when enabled; every other
        # non-digest callback (speaker labelling, etc.) goes to speaker_cb.
        if clip_callback is not None and data.startswith("clip:"):
            await clip_callback(
                callback_query_id=callback_query_id,
                message_id=message_id, data=data,
            )
            return
        await speaker_cb(
            callback_query_id=callback_query_id,
            message_id=message_id, data=data,
        )

    async def on_callback_query(cbq_id: str, msg_id: int, data: str) -> None:
        await dispatch_callback_query(
            callback_query_id=cbq_id, message_id=msg_id, data=data,
            digest_handler=digest_handler,
            fallback_handler=_composed_fallback,
        )

    on_text_reply = make_text_reply_handler(
        email_db=email_db, config=config, bot=bot,
    )

    async def _composed_text_reply(reply_to_message_id: int, text: str) -> None:
        # Clippings clarification replies are tried first — clip_text_reply
        # only consumes a reply that matches a pending clarification (exact
        # message-id, or the single unambiguous pending one). Anything it
        # does not consume falls through to speaker-labelling text replies.
        if clip_text_reply is not None:
            if await clip_text_reply(reply_to_message_id, text):
                return
            log.info(
                "Clip text reply (reply_to=%s) matched no unambiguous pending "
                "clarification — falling through to speaker labelling",
                reply_to_message_id,
            )
        await on_text_reply(reply_to_message_id, text)

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
                    on_callback_query=on_callback_query,
                    on_labeling_reply=_composed_text_reply,
                ),
                on_persistent_failure=on_supervised_task_crashloop,
            ))
            tg.create_task(supervise(
                "speaker-reaper",
                lambda: run_speaker_reaper_forever(email_db, config, bot),
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


NewsStageFn = Callable[[date], Awaitable[None]]


def _build_news_chain_fn(
    *,
    master_fn: NewsStageFn,
    research_fn: NewsStageFn | None,
    digest_fn: NewsStageFn | None,
    master_completed: Callable[[date], Awaitable[bool]],
) -> NewsStageFn:
    """Compose the daily news chain: master → research → digest.

    research_fn and digest_fn are optional (None when disabled). research
    and digest run only when master_completed(d) is True. A research
    failure is logged and swallowed so it can never block the digest —
    research is best-effort enrichment (see the auto-research design, D2).
    """
    async def _chain(d: date) -> None:
        await master_fn(d)
        if not await master_completed(d):
            return
        if research_fn is not None:
            try:
                await research_fn(d)
            except Exception:
                log.exception("Research run failed for %s", d)
        if digest_fn is not None:
            try:
                await digest_fn(d)
            except Exception:
                log.exception("Digest run failed for %s", d)
    return _chain


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
            #
            # Digest text is rendered as Telegram HTML (<b>, <i>, bare-URL
            # auto-link). We send with parse_mode="HTML" and fall back to
            # plain text on a 400 — so an unexpected entity in agent output
            # degrades gracefully (visible tags) rather than dropping the
            # whole message. Matches the trace.py "_send_html" pattern.
            ids: list[int | None] = []
            for text, kb in messages:
                try:
                    msg_id = await send_message_return_id(
                        text, bot,
                        thread_id=config.news_daily_telegram_topic_id,
                        parse_mode="HTML",
                        reply_markup=kb,
                    )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 400:
                        log.warning(
                            "Digest HTML send rejected (400); retrying plain",
                        )
                        try:
                            msg_id = await send_message_return_id(
                                text, bot,
                                thread_id=config.news_daily_telegram_topic_id,
                                reply_markup=kb,
                            )
                        except Exception:
                            log.exception(
                                "Plain-text fallback also failed for digest "
                                "message",
                            )
                            ids.append(None)
                            continue
                    else:
                        log.exception("Failed to send digest message")
                        ids.append(None)
                        continue
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

    research_run_for_date_fn = None
    if config.news_research_enabled:
        from .news_research.runner import (
            RunnerConfig as ResearchRunnerConfig,
            run_for_date as _research_run_for_date,
            run_agent_via_agent_infra as _research_run_agent,
        )
        from .news_research.state import NewsResearchStateDB

        research_db = NewsResearchStateDB(config.email_ingest_state_db_path)
        await research_db.init_db()
        research_runner_cfg = ResearchRunnerConfig(
            vault_root=config.pkm_vault_path,
            model=config.news_research_model,
            max_items=config.news_research_max_items,
            max_turns=config.news_research_max_turns,
            feedback_window_days=(
                config.news_personal_digest_feedback_window_days
            ),
        )

        # Ratings come from the digest DB when the digest is enabled;
        # otherwise an empty provider keeps news_research decoupled and
        # functional (interests-profile + judgment only).
        if digest_db is not None:
            async def _recent_ratings(start, end):
                return await digest_db.recent_ratings(start, end)
        else:
            async def _recent_ratings(start, end):
                return []

        async def research_run_for_date_fn(d) -> None:
            await _research_run_for_date(
                d, db=research_db, config=research_runner_cfg,
                run_agent=_research_run_agent,
                recent_ratings=_recent_ratings,
            )

    async def _master_only(d) -> None:
        await run_for_date(
            d, db=db, config=runner_cfg,
            run_agent=run_agent_via_agent_infra,
            notify=notify,
        )

    async def _master_completed(d) -> bool:
        row = await db.get_run(d)
        if row is None or row.get("status") != "completed":
            log.info(
                "Skipping research/digest for %s — master status=%s",
                d, row.get("status") if row else None,
            )
            return False
        return True

    run_for_date_fn = _build_news_chain_fn(
        master_fn=_master_only,
        research_fn=research_run_for_date_fn,
        digest_fn=digest_run_for_date_fn,
        master_completed=_master_completed,
    )

    h, m = config.news_daily_master_local_time.split(":", 1)
    fire = _time(int(h), int(m))
    tz = _resolve_news_daily_tz(config.news_daily_master_tz)

    sched = NewsDailyScheduler(
        db=db, run_for_date_fn=run_for_date_fn, notify=notify,
        fire_time=fire, tz=tz,
        backfill_window_days=config.news_daily_master_backfill_days,
    )
    await sched.run_forever()


async def _run_news_weekly_patterns_path(config: DaemonConfig) -> None:
    """Independent weekly pattern-recognition scheduler.

    NOT chained with the daily master/research/digest path — it has its
    own weekly cadence and reads whatever daily masters exist. Mirrors the
    ADR-005 in-daemon pattern at a weekly cadence (see ADR-010).
    """
    from datetime import time as _time
    from .news_weekly_patterns.runner import (
        RunnerConfig as WeeklyRunnerConfig,
        run_for_iso_week,
        run_agent_via_agent_infra as _weekly_run_agent,
    )
    from .news_weekly_patterns.scheduler import NewsWeeklyScheduler
    from .news_weekly_patterns.state import NewsWeeklyStateDB

    db = NewsWeeklyStateDB(config.email_ingest_state_db_path)
    await db.init_db()

    bot = BotConfig(
        bot_token=config.telegram_bot_token,
        chat_id=config.telegram_chat_id,
    )

    # Design D4: dedicated weekly topic; fall back to the daily topic
    # when no dedicated topic is configured.
    weekly_topic_id = (
        config.news_weekly_telegram_topic_id
        if config.news_weekly_telegram_topic_id is not None
        else config.news_daily_telegram_topic_id
    )

    async def notify(text: str) -> None:
        try:
            await send_message(
                text, bot,
                thread_id=weekly_topic_id,
            )
        except Exception:
            log.exception("Failed to send news weekly notification")

    runner_cfg = WeeklyRunnerConfig(
        vault_root=config.pkm_vault_path,
        model=config.news_weekly_model,
        min_days=config.news_weekly_min_days,
        recurrence_threshold=config.news_weekly_recurrence_threshold,
        max_threads=config.news_weekly_max_threads,
        max_turns=config.news_weekly_max_turns,
    )

    # Ratings come from the digest DB when the digest is enabled;
    # otherwise an empty provider keeps weekly decoupled and functional.
    if config.news_personal_digest_enabled:
        from .news_personal_digest.state import NewsDigestStateDB

        digest_db = NewsDigestStateDB(config.email_ingest_state_db_path)
        await digest_db.init_db()

        async def _recent_ratings(start, end):
            return await digest_db.recent_ratings(start, end)
    else:
        async def _recent_ratings(start, end):
            return []

    async def run_for_week_fn(iso_week: str) -> None:
        await run_for_iso_week(
            iso_week, db=db, config=runner_cfg,
            run_agent=_weekly_run_agent,
            recent_ratings=_recent_ratings,
            notify=notify,
        )

    h, m = config.news_weekly_fire_time.split(":", 1)
    fire = _time(int(h), int(m))
    # Reuse the daily-master TZ — no separate weekly TZ config field.
    tz = _resolve_news_daily_tz(config.news_daily_master_tz)

    sched = NewsWeeklyScheduler(
        db=db,
        run_for_week_fn=run_for_week_fn,
        notify=notify,
        weekday=config.news_weekly_fire_weekday,
        fire_time=fire,
        tz=tz,
        backfill_window_weeks=config.news_weekly_backfill_weeks,
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


def _clippings_routing_targets(vault_root: Path) -> list[str]:
    """Enumerate active project indexes + plans as clipping routing targets."""
    targets: list[str] = []
    projects_root = vault_root / "01-Projects"
    if not projects_root.is_dir():
        return targets
    for proj in sorted(p for p in projects_root.iterdir() if p.is_dir()):
        idx = proj / "_index.md"
        if idx.exists():
            targets.append(str(idx.relative_to(vault_root)))
        plans = proj / "plans"
        if plans.is_dir():
            targets += [
                str(p.relative_to(vault_root))
                for p in sorted(plans.glob("*.md"))
            ]
    return targets


async def _run_clippings_path(config: DaemonConfig) -> None:
    from .clippings_telegram import build_clarification_keyboard

    state_db = ClippingsStateDB(config.email_ingest_state_db_path)
    await state_db.init_db()
    bot = BotConfig(
        bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id,
    )
    clippings_dir = config.pkm_vault_path / config.clippings_dir

    async def telegram_notifier(text: str, *, thread_id: int | None = None) -> None:
        await send_message(text, bot, thread_id=thread_id)

    async def send_clarification(
        *, question: str, candidates: list[str], clipping_name: str,
    ) -> int | None:
        # Keyboard's embedded msg_id is vestigial — handle_clip_callback
        # correlates via the real Telegram message_id (stored by
        # set_pending_clarification) using find_pending_clarification_by_message.
        kb = build_clarification_keyboard(msg_id=0, candidates=candidates)
        return await send_message_return_id(
            f"❓ Clipping needs a home: {clipping_name}\n{question}",
            bot, thread_id=config.news_telegram_topic_id, reply_markup=kb,
        )

    async def on_persistent_failure(task_name: str, failures: int) -> None:
        try:
            await send_message(
                f"⚠️ Clippings watcher crash-looping\n\n"
                f"Task: {task_name}\nConsecutive failures: {failures}\n"
                f"Will keep restarting with backoff. Check daemon logs.",
                bot, thread_id=config.news_telegram_topic_id,
            )
        except Exception:
            log.exception("Failed to send clippings crash-loop alert")

    async def factory() -> None:
        await run_clippings_watch_loop(
            clippings_dir=clippings_dir,
            vault_root=config.pkm_vault_path,
            state=state_db,
            telegram_notifier=telegram_notifier,
            list_routing_targets=lambda: _clippings_routing_targets(config.pkm_vault_path),
            send_clarification=send_clarification,
            settle_s=float(config.clippings_settle_seconds),
            reconcile_interval_s=float(config.clippings_reconcile_interval_seconds),
            max_failed_retries=config.clippings_max_failed_retries,
            news_topic_id=config.news_telegram_topic_id,
            model=config.clippings_model,
        )

    await supervise(
        "clippings-watcher", factory,
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
