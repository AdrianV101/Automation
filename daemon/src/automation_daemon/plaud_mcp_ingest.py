"""Plaud MCP ingest: poll loop, per-recording ingest and manual ingest."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from pkm import write_raw_transcript

from .config import DaemonConfig
from .pipeline import process_recording
from .plaud_mcp_adapter import is_ready, speaker_map, to_recording_job
from .plaud_mcp_attention import render_attention, write_attention_note
from .plaud_mcp_client import PlaudAuthError, PlaudMcpClient, PlaudMcpError
from .plaud_mcp_state import PlaudMcpStateDB, PlaudStatus
from .plaud_mcp_trigger import decide, has_generic

log = logging.getLogger(__name__)

_MCP_PROBLEM_THRESHOLD = 3
_AUTH_HINT = (
    " The Plaud login may have expired: run the Plaud MCP login"
    " (e.g. from Claude Code) on the Mac mini."
)


@dataclass(frozen=True)
class PlaudMcpSettings:
    switchover: date
    window_days: int = 14
    quiet_s: int = 600
    fallback_s: int = 86400
    max_attempts: int = 3


class OutcomeTracker:
    """StatusTracker that records the pipeline outcome instead of notifying."""

    def __init__(self) -> None:
        self.routed: bool = False
        self.error: str | None = None
        self.transcript_path: str | None = None

    async def update(self, status: str, **kwargs: str | None) -> None:
        if status == "completed":
            self.routed = True
            self.error = kwargs.get("error") or None
            self.transcript_path = kwargs.get("transcript_path") or self.transcript_path
        elif status == "failed":
            self.error = kwargs.get("error") or "pipeline failed"


@dataclass
class CycleReport:
    ingested: list[str]
    waiting: int
    failed: list[str]
    fetch_errors: dict[str, str] = field(default_factory=dict)


async def run_poll_cycle(
    client,
    state: PlaudMcpStateDB,
    config: DaemonConfig,
    settings: PlaudMcpSettings,
    *,
    now: datetime,
    process_recording_fn=process_recording,
) -> CycleReport:
    report = CycleReport(ingested=[], waiting=0, failed=[])
    today = now.astimezone(UTC).date()
    date_from = max(settings.switchover, today - timedelta(days=settings.window_days))
    if date_from > today:
        return report

    listing = await client.list_files(date_from, today)
    for item in listing:
        file_id: str | None = None
        try:
            file_id = item["id"]
            row = await state.get(file_id)
            if row is not None and row.status in ("completed", "failed", "ingesting"):
                continue
            detail = await client.get_file(file_id)
            if not is_ready(detail):
                continue
            segments = await client.get_transcript(file_id)
            if not segments:
                continue
            ingest_now, track = decide(
                row.track if row is not None else None,
                speaker_map(segments),
                now,
                quiet_s=settings.quiet_s,
                fallback_s=settings.fallback_s,
            )
            name = detail.get("name") or item.get("name") or ""
            await state.upsert_waiting(file_id, name, track, now)
            if not ingest_now:
                report.waiting += 1
                continue
            status = await ingest_recording(
                client, state, config, detail, segments,
                now=now, max_attempts=settings.max_attempts,
                process_recording_fn=process_recording_fn,
            )
        except PlaudAuthError:
            raise
        except PlaudMcpError as e:
            log.error("Plaud MCP error for recording %s, skipping: %s", file_id, e)
            if file_id is not None:
                report.fetch_errors[file_id] = f"{type(e).__name__}: {e}"
            continue
        except Exception as e:
            log.exception("Unexpected error for Plaud recording %s, skipping", file_id)
            if file_id is not None:
                report.fetch_errors[file_id] = f"{type(e).__name__}: {e}"
            continue

        if status == "completed":
            report.ingested.append(file_id)
        elif status == "failed":
            report.failed.append(file_id)
        else:
            report.waiting += 1
    return report


async def ingest_recording(
    client,
    state: PlaudMcpStateDB,
    config: DaemonConfig,
    file_detail: dict,
    segments: list[dict],
    *,
    now: datetime,
    max_attempts: int,
    process_recording_fn=process_recording,
) -> PlaudStatus:
    file_id = file_detail["id"]
    attempts = await state.mark_ingesting(file_id)
    tracker = OutcomeTracker()
    raised: BaseException | None = None
    try:
        marks = await client.get_marks(file_id)
        job = to_recording_job(file_detail, segments, marks)
        await process_recording_fn(
            job,
            config,
            status=tracker,
            speakers_unresolved=has_generic(speaker_map(segments)),
            notify=False,
        )
    except Exception as e:
        log.exception("Ingest of Plaud recording %s raised", file_id)
        raised = e

    if raised is None and tracker.routed:
        if tracker.error is None:
            await state.mark_completed(file_id, tracker.transcript_path, now)
            log.info("Ingested Plaud recording %s", file_id)
            return "completed"
        await state.mark_failed(file_id, f"extraction: {tracker.error}")
        log.error("Extraction failed for Plaud recording %s", file_id)
        return "failed"

    if raised is not None:
        error = f"{type(raised).__name__}: {raised}"
    else:
        error = tracker.error or "pipeline did not complete"

    if attempts < max_attempts:
        await state.mark_retry(file_id, error)
        log.warning(
            "Ingest of Plaud recording %s failed (attempt %d/%d), will retry",
            file_id, attempts, max_attempts,
        )
        result: PlaudStatus = "waiting_labels"
    else:
        await state.mark_failed(file_id, error)
        log.error(
            "Ingest of Plaud recording %s failed after %d attempts", file_id, attempts,
        )
        result = "failed"

    if isinstance(raised, PlaudAuthError):
        raise raised
    return result


def _default_client_factory(config: DaemonConfig) -> Callable[[], AbstractAsyncContextManager]:
    return lambda: PlaudMcpClient(config.plaud_mcp_package)


async def run_plaud_mcp_forever(
    config: DaemonConfig,
    *,
    client_factory: Callable[[], AbstractAsyncContextManager] | None = None,
    process_recording_fn=process_recording,
    sleep=asyncio.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    if config.plaud_mcp_switchover_date is None:
        raise ValueError("plaud_mcp_switchover_date is required for Plaud MCP ingest")
    settings = PlaudMcpSettings(
        switchover=config.plaud_mcp_switchover_date,
        window_days=config.plaud_mcp_window_days,
        quiet_s=config.plaud_mcp_label_quiet_s,
        fallback_s=config.plaud_mcp_unlabelled_fallback_s,
    )
    factory = client_factory or _default_client_factory(config)

    state = PlaudMcpStateDB(config.email_ingest_state_db_path)
    await state.init_db()

    consecutive_failures = 0
    fetch_failures: dict[str, int] = {}
    interval = config.plaud_mcp_poll_interval_s
    while True:
        started = clock()
        mcp_problem: str | None = None
        problem_lines: list[str] = []
        try:
            recovered = await state.recover_interrupted()
            if recovered:
                log.error(
                    "Marked %d interrupted Plaud ingests as failed: %s",
                    len(recovered), recovered,
                )
            async with factory() as client:
                report = await run_poll_cycle(
                    client, state, config, settings,
                    now=started, process_recording_fn=process_recording_fn,
                )
            consecutive_failures = 0
            log.info(
                "Plaud MCP poll: %d ingested, %d waiting, %d failed",
                len(report.ingested), report.waiting, len(report.failed),
            )
            fetch_failures = {
                file_id: fetch_failures.get(file_id, 0) + 1
                for file_id in report.fetch_errors
            }
            for file_id, error in report.fetch_errors.items():
                count = fetch_failures[file_id]
                if count >= _MCP_PROBLEM_THRESHOLD:
                    problem_lines.append(
                        f"Recording {file_id} could not be read from Plaud "
                        f"for {count} polls: {error}"
                    )
        except PlaudMcpError as e:
            consecutive_failures += 1
            log.error(
                "Plaud MCP poll failed (%d consecutive): %s", consecutive_failures, e,
            )
            if consecutive_failures >= _MCP_PROBLEM_THRESHOLD:
                line = (
                    f"Plaud MCP failing for {consecutive_failures} consecutive polls: {e}"
                )
                if isinstance(e, PlaudAuthError):
                    line += _AUTH_HINT
                problem_lines.append(line)
        except Exception as e:
            consecutive_failures += 1
            log.exception(
                "Plaud ingest poll failed (%d consecutive)", consecutive_failures,
            )
            if consecutive_failures >= _MCP_PROBLEM_THRESHOLD:
                problem_lines.append(
                    f"Plaud ingest failing for {consecutive_failures} consecutive "
                    f"polls: {type(e).__name__}: {e}"
                )
        if problem_lines:
            mcp_problem = "\n\n".join(problem_lines)

        try:
            write_attention_note(
                config.pkm_vault_path,
                render_attention(await state.list_failed(), mcp_problem),
            )
        except Exception:
            log.exception("Failed to write Plaud ingest attention note")
        elapsed = (clock() - started).total_seconds()
        await sleep(max(0.0, interval - elapsed))


async def run_manual_ingest(
    config: DaemonConfig,
    file_id: str,
    *,
    force: bool,
    extract: bool,
    client_factory: Callable[[], AbstractAsyncContextManager] | None = None,
    process_recording_fn=process_recording,
    out: Callable[[str], None] = print,
) -> int:
    factory = client_factory or _default_client_factory(config)
    state = PlaudMcpStateDB(config.email_ingest_state_db_path)
    await state.init_db()
    now = datetime.now(UTC)

    async with factory() as client:
        detail = await client.get_file(file_id)
        segments = await client.get_transcript(file_id) if is_ready(detail) else []
        if not segments:
            out(f"Recording {file_id} is not ready (no transcript or summary yet).")
            return 1

        row = await state.get(file_id)
        if row is not None and row.status == "ingesting":
            out(f"Recording {file_id} is being ingested right now; try again later.")
            return 1
        current_map = speaker_map(segments)
        if row is not None and row.status == "completed":
            if not force:
                out(
                    "Already ingested; use --force to rewrite the transcript "
                    "(add --extract to re-run extraction)."
                )
                return 1
            if not extract:
                marks = await client.get_marks(file_id)
                job = to_recording_job(detail, segments, marks)
                path = write_raw_transcript(
                    job.transcript_data, config.pkm_vault_path, source="plaud-mcp",
                    speakers_unresolved=has_generic(current_map),
                )
                await state.mark_completed(file_id, str(path), now)
                out(str(path))
                return 0

        if row is not None and row.status == "completed":
            out(
                "Warning: re-running extraction may duplicate notes already routed "
                "for this recording."
            )

        _, track = decide(
            row.track if row is not None else None,
            current_map,
            now,
            quiet_s=config.plaud_mcp_label_quiet_s,
            fallback_s=config.plaud_mcp_unlabelled_fallback_s,
        )
        await state.upsert_waiting(file_id, detail.get("name") or "", track, now)
        status = await ingest_recording(
            client, state, config, detail, segments,
            now=now, max_attempts=1,
            process_recording_fn=process_recording_fn,
        )

    if status == "completed":
        updated = await state.get(file_id)
        out(f"Ingested {file_id}: {updated.transcript_path if updated else ''}")
        return 0
    updated = await state.get(file_id)
    out(f"Ingest of {file_id} did not complete (status {status}): "
        f"{updated.error if updated else ''}")
    return 1
