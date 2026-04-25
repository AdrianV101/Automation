"""WebSocket connection management and event dispatching for Plaud API."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import websockets
from websockets.asyncio.client import connect

from .client import PlaudClient, PlaudRecording
from .downloader import download_new_recordings
from .state import DownloadedRecording, PlaudStateDB

log = logging.getLogger(__name__)

RECONNECT_DELAY_INITIAL = 5
RECONNECT_DELAY_MAX = 300
PERSISTENT_FAILURE_THRESHOLD = 5
PERSISTENT_FAILURE_TIMEOUT = 900  # 15 minutes


def _format_download_summary(downloaded: list[tuple[PlaudRecording, list[dict]]]) -> str:
    """Format a human-readable summary of successfully downloaded recordings."""
    lines = []
    total_duration_ms = 0
    for recording, _segments in downloaded:
        duration_min = recording.duration_ms // 60000
        lines.append(f"  * {recording.filename} ({duration_min} min)")
        total_duration_ms += recording.duration_ms
    total_min = total_duration_ms // 60000
    file_list = "\n".join(lines)
    return (
        f"\U0001f4e5 Plaud Transcript Fetched\n\n"
        f"{len(downloaded)} new recording(s):\n"
        f"{file_list}\n\n"
        f"Total: {total_min} min of audio"
    )


def _format_download_failures(failures: list[tuple[PlaudRecording, str]]) -> str:
    """Format a human-readable summary of failed downloads."""
    lines = [f"  * {rec.filename}: {error}" for rec, error in failures]
    file_list = "\n".join(lines)
    return (
        "\u26a0\ufe0f Plaud Transcript Fetch Failed\n\n"
        f"{len(failures)} recording(s) failed:\n"
        f"{file_list}\n\n"
        "Will retry on next sync."
    )


class PlaudWebSocket:
    """WebSocket connection to Plaud with auto-reconnect and backoff."""

    def __init__(self, token: str, id_hash: str, base_url: str):
        self.token = token
        self.id_hash = id_hash
        # Convert https:// to wss://
        ws_base = base_url.replace("https://", "wss://")
        self.ws_url = f"{ws_base}/ws/notify?platform=web&hash_id={id_hash}"
        self._reconnect_delay = RECONNECT_DELAY_INITIAL
        self._consecutive_failures = 0
        self._last_connected = time.time()

    def _connect(self):
        """Return async context manager for WebSocket connection."""
        return connect(
            self.ws_url,
            subprotocols=[self.token],
            origin="https://web.plaud.ai",
        )

    async def listen(
        self,
        on_event: Callable[[dict], Awaitable[None]],
        on_persistent_failure: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """
        Main listen loop with auto-reconnect.
        Calls on_event(parsed_json) for each message.
        Calls on_persistent_failure after 5 consecutive failures or 15 min disconnected.
        """
        persistent_failure_notified = False

        while True:
            try:
                async with self._connect() as ws:
                    log.info("WebSocket connected to Plaud")
                    self._reconnect_delay = RECONNECT_DELAY_INITIAL
                    self._consecutive_failures = 0
                    self._last_connected = time.time()
                    persistent_failure_notified = False

                    async for message in ws:
                        try:
                            event = json.loads(message)
                        except json.JSONDecodeError:
                            log.warning("Non-JSON message: %s", message[:100])
                            continue
                        try:
                            await on_event(event)
                        except Exception:
                            log.exception("Error in event handler")

            except websockets.ConnectionClosed as e:
                log.warning("WebSocket closed: %s, reconnecting...", e)
                self._consecutive_failures += 1
            except Exception as e:
                log.exception("WebSocket error: %s", e)
                self._consecutive_failures += 1

            # Check for persistent failure condition
            time_disconnected = time.time() - self._last_connected
            if (
                on_persistent_failure
                and not persistent_failure_notified
                and (
                    self._consecutive_failures >= PERSISTENT_FAILURE_THRESHOLD
                    or time_disconnected >= PERSISTENT_FAILURE_TIMEOUT
                )
            ):
                log.error(
                    "Persistent WebSocket failure: %d consecutive failures, %.0fs disconnected",
                    self._consecutive_failures,
                    time_disconnected,
                )
                try:
                    await on_persistent_failure()
                    persistent_failure_notified = True
                except Exception as e:
                    log.error("Failed to send persistent failure notification: %s", e)

            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, RECONNECT_DELAY_MAX)


async def run_websocket_loop(
    client: PlaudClient,
    state_db: PlaudStateDB,
    id_hash: str,
    download_dir: Path,
    *,
    on_new_recording: Callable[[DownloadedRecording], Awaitable[None]] | None = None,
    on_status: Callable[[str], Awaitable[None]] | None = None,
    on_persistent_failure: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """
    Callback-driven WebSocket loop for Plaud recording sync.

    Catches up on missed recordings at startup, then listens for new ones
    via WebSocket. Calls ``on_new_recording`` for each recording that needs
    processing, and ``on_status`` with human-readable status messages about
    downloads.

    This function owns only Plaud-specific concerns: connection, download,
    and status formatting. All daemon-level orchestration (pipeline processing,
    Telegram notifications, token validation) belongs in the caller.
    """
    # --- Startup catch-up: download any missed recordings ---
    log.info("Checking for missed recordings on startup...")
    try:
        downloaded, failed = await download_new_recordings(
            client, state_db, download_dir
        )
        if downloaded:
            log.info("Fetched %d missed recording(s) on startup", len(downloaded))
            if on_status:
                try:
                    await on_status(_format_download_summary(downloaded))
                except Exception as e:
                    log.error("on_status callback failed: %s", e)
        if failed:
            log.warning("%d recording(s) failed to fetch on startup", len(failed))
            if on_status:
                try:
                    await on_status(_format_download_failures(failed))
                except Exception as e:
                    log.error("on_status callback failed: %s", e)
    except Exception:
        log.exception("Error during startup download check")

    # --- Notify caller about any pending recordings ---
    try:
        pending = await state_db.get_pending_processing()
        if pending:
            log.info("Found %d recording(s) pending processing", len(pending))
            if on_new_recording:
                for rec in pending:
                    try:
                        await on_new_recording(rec)
                    except Exception:
                        log.exception(
                            "on_new_recording callback failed for %s", rec.recording_id
                        )
    except Exception:
        log.exception("Error during startup processing catch-up")

    # --- WebSocket event handler ---
    async def handle_event(event: dict) -> None:
        log.debug("Received WebSocket event: %s", event)
        try:
            dl, fl = await download_new_recordings(client, state_db, download_dir)
            if dl:
                log.info("Fetched %d new recording(s)", len(dl))
                if on_status:
                    try:
                        await on_status(_format_download_summary(dl))
                    except Exception as e:
                        log.error("on_status callback failed: %s", e)
                # Notify caller for each newly downloaded recording
                if on_new_recording:
                    new_pending = await state_db.get_pending_processing()
                    pending_by_id = {r.recording_id: r for r in new_pending}
                    for rec, _segments in dl:
                        db_rec = pending_by_id.get(rec.id)
                        if db_rec:
                            try:
                                await on_new_recording(db_rec)
                            except Exception:
                                log.exception(
                                    "on_new_recording callback failed for %s",
                                    db_rec.recording_id,
                                )
            if fl:
                log.warning("%d recording(s) failed to fetch", len(fl))
                if on_status:
                    try:
                        await on_status(_format_download_failures(fl))
                    except Exception as e:
                        log.error("on_status callback failed: %s", e)
        except Exception:
            log.exception("Error handling WebSocket event")

    # --- Start WebSocket listener ---
    ws_client = PlaudWebSocket(client.token, id_hash, client.base_url)
    log.info("Starting Plaud WebSocket listener at %s", ws_client.ws_url)
    await ws_client.listen(handle_event, on_persistent_failure=on_persistent_failure)
