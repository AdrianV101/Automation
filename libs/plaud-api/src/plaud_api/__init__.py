"""Reverse-engineered Plaud Note API client (REST + WebSocket)."""

from .client import PlaudClient, PlaudRecording
from .state import PlaudStateDB, DownloadedRecording
from .downloader import download_new_recordings
from .transcript import plaud_segments_to_transcript, Transcript, TranscriptSegment
from .websocket import PlaudWebSocket, run_websocket_loop

__all__ = [
    "PlaudClient", "PlaudRecording",
    "PlaudStateDB", "DownloadedRecording",
    "download_new_recordings",
    "plaud_segments_to_transcript", "Transcript", "TranscriptSegment",
    "PlaudWebSocket", "run_websocket_loop",
]
