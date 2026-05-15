"""Processing pipeline: transcript -> PKM -> agent extraction -> notify."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from pkm import write_raw_transcript
from telegram_interface import BotConfig, create_forum_topic

from agent_infra import AgentInfraConfigError
from telegram_interface import send_message

from .capture import agent_capture_session
from .config import DaemonConfig
from .extraction import agent_extract_and_route
from .models import RecordingJob
from .notifications import send_routing_summary, send_transcription_error

if TYPE_CHECKING:
    from .models import StatusTracker

log = logging.getLogger(__name__)


async def _notify_status(status: "StatusTracker | None", state: str, **kwargs: str | None) -> None:
    if status is not None:
        await status.update(state, **kwargs)


async def process_recording(
    job: RecordingJob,
    config: DaemonConfig,
    status: "StatusTracker | None" = None,
    speakers_unresolved: bool = False,
) -> None:
    """Run the pipeline for a single recording. Logs errors rather than raising."""
    log.info("Processing recording %s (%s)", job.filename, job.id)

    bot = BotConfig(bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id)

    try:
        recorded_dt = datetime.fromisoformat(job.recorded_at)
        rec_topic = f"Rec: {recorded_dt.strftime('%m/%d')} {job.filename[:20]}"
    except (ValueError, AttributeError):
        log.warning("Could not parse recorded_at %r, using filename", job.recorded_at)
        rec_topic = f"Rec: {job.filename[:30]}"
    rec_thread_id = await create_forum_topic(rec_topic, bot)

    try:
        transcript = job.transcript_data

        await _notify_status(status, "writing_pkm")
        transcript_path = write_raw_transcript(
            transcript, config.pkm_vault_path, source=job.source,
            speakers_unresolved=speakers_unresolved,
        )

        await _notify_status(status, "extracting")
        try:
            routing_result = await agent_extract_and_route(
                transcript,
                transcript_path,
                config.pkm_vault_path,
                tg=bot,
                thread_id=rec_thread_id,
                source_metadata=job.source_metadata,
            )
        except Exception:
            log.exception("Agent routing failed for %s, transcript preserved", job.id)
            routing_result = None

        if routing_result and routing_result.success:
            await _notify_status(
                status, "completed",
                transcript_path=str(transcript_path),
                summary_path=routing_result.summary_path or "",
            )
        else:
            error_msg = (
                routing_result.error if routing_result else "Agent routing failed"
            )
            await _notify_status(
                status, "completed",
                transcript_path=str(transcript_path),
                error=f"Agent routing failed; transcript preserved: {error_msg}",
            )

        try:
            await send_routing_summary(routing_result, bot, thread_id=rec_thread_id)
        except Exception:
            log.exception("Failed to send Telegram summary for %s", job.id)

        if (
            config.enable_session_capture
            and routing_result is not None
            and routing_result.success
            and routing_result.files_written
        ):
            try:
                capture_result = await agent_capture_session(
                    routing_result,
                    transcript_path,
                    config.pkm_vault_path,
                    tg=bot,
                    thread_id=rec_thread_id,
                )
                if not capture_result.success:
                    # Operator visibility: enrich the log with the
                    # auditable context (turns, files-attempted, summary
                    # path) and surface a one-line note in the same forum
                    # thread that has the routing summary so the user who
                    # opted into capture knows it failed without scraping
                    # daemon logs.
                    log.warning(
                        "Session capture for job=%s did not append devlog: "
                        "error=%r turns_used=%d files_appended=%r summary_path=%s",
                        job.id, capture_result.error, capture_result.turns_used,
                        capture_result.files_appended, routing_result.summary_path,
                    )
                    try:
                        await send_message(
                            f"⚠️ Devlog capture pass failed: "
                            f"{capture_result.error or 'unknown'}. "
                            "Extraction succeeded; transcript and notes preserved.",
                            bot, thread_id=rec_thread_id,
                        )
                    except Exception:
                        log.exception(
                            "Failed to notify capture failure for %s", job.id,
                        )
            except AgentInfraConfigError:
                # Daemon-wide misconfiguration (env var missing, node not on
                # PATH). Don't swallow as a per-job capture failure -- this
                # will fail every job, and the operator needs to fix the host.
                # Propagate to the outer pipeline handler which surfaces a
                # "failed" status and a clear Telegram error notification.
                raise
            except Exception:
                log.exception(
                    "Session capture failed for %s, extraction unaffected", job.id,
                )
                try:
                    await send_message(
                        "⚠️ Devlog capture pass crashed. "
                        "Extraction succeeded; transcript and notes preserved.",
                        bot, thread_id=rec_thread_id,
                    )
                except Exception:
                    log.exception(
                        "Failed to notify capture crash for %s", job.id,
                    )

        log.info("Completed processing %s", job.filename)

    except Exception as e:
        error_msg = str(e)
        log.exception("Failed processing %s", job.filename)
        try:
            await _notify_status(status, "failed", error=error_msg)
        except Exception:
            log.exception("Failed to update status for %s", job.id)
        try:
            await send_transcription_error(job.filename, error_msg, bot, thread_id=rec_thread_id)
        except Exception:
            log.exception("Failed to send error notification for %s", job.id)
