"""Convert Plaud MCP file details and transcripts into RecordingJob."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from pkm import TranscriptData, TranscriptSegment

from .models import RecordingJob

log = logging.getLogger(__name__)

_PLAUD_LINK = re.compile(r"!?\[[^\]]*\]\(plaud://[^)]*\)")
_MARK_TIME_KEYS = ("start_time", "time", "timestamp", "mark_time")


def is_ready(file_detail: dict) -> bool:
    has_transaction = any(
        s.get("data_type") == "transaction"
        for s in file_detail.get("source_list") or []
    )
    has_summary = any(
        n.get("data_type") == "auto_sum_note"
        for n in file_detail.get("note_list") or []
    )
    return has_transaction and has_summary


def speaker_map(segments: list[dict]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for seg in segments:
        speaker = seg.get("speaker")
        original = seg.get("original_speaker") or speaker
        if original is None or speaker is None:
            continue
        mapping.setdefault(original, speaker)
    return mapping


def strip_plaud_links(md: str) -> str:
    return _PLAUD_LINK.sub("", md)


def _parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _mark_time_ms(mark: dict) -> float | None:
    for key in _MARK_TIME_KEYS:
        value = mark.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _highlight_text(time_ms: float, segments: list[dict]) -> str:
    for seg in segments:
        if seg["start_time"] <= time_ms <= seg["end_time"]:
            return seg["content"]
    nearest = min(segments, key=lambda s: abs(s["start_time"] - time_ms))
    return nearest["content"]


def to_recording_job(
    file_detail: dict, segments: list[dict], marks: list[dict],
) -> RecordingJob:
    file_id = file_detail["id"]
    recorded_at = _parse_utc(file_detail["start_at"]).isoformat()
    duration = file_detail.get("duration")

    ts_segments = [
        TranscriptSegment(
            start=s["start_time"] / 1000,
            end=s["end_time"] / 1000,
            speaker=s.get("speaker") or "Unknown",
            text=s["content"],
        )
        for s in segments
    ]
    speakers: list[str] = []
    for seg in ts_segments:
        if seg.speaker not in speakers:
            speakers.append(seg.speaker)
    transcript_data = TranscriptData(
        job_id=file_id,
        recorded_at=recorded_at,
        duration_seconds=duration / 1000 if duration is not None else None,
        speakers=speakers,
        segments=ts_segments,
        full_text="\n\n".join(s.text for s in ts_segments),
    )

    source_metadata: dict[str, object] = {}

    summaries: dict[str, str] = {}
    for note in file_detail.get("note_list") or []:
        content = strip_plaud_links(note.get("data_content") or "").strip()
        if not content:
            continue
        key = note.get("data_tab_name") or note.get("data_title") or note.get("data_type")
        base, n = key, 2
        while key in summaries:
            key = f"{base} ({n})"
            n += 1
        summaries[key] = content
    if summaries:
        source_metadata["plaud_summaries"] = summaries

    for src in file_detail.get("source_list") or []:
        if src.get("data_type") == "outline" and src.get("data_content"):
            source_metadata["plaud_outline"] = src["data_content"]
            break

    highlights: list[dict[str, object]] = []
    if marks and not segments:
        log.warning(
            "plaud-mcp: %d highlight marks but no transcript segments to anchor them",
            len(marks),
        )
    else:
        for mark in marks:
            time_ms = _mark_time_ms(mark)
            if time_ms is None:
                log.warning(
                    "plaud-mcp: skipping mark without usable time; keys=%s",
                    sorted(mark.keys()),
                )
                continue
            highlights.append(
                {"time_s": time_ms / 1000, "text": _highlight_text(time_ms, segments)}
            )
        if marks and not highlights:
            raise ValueError(
                "Plaud highlight marks have no usable time field; "
                f"keys={sorted(marks[0].keys())}"
            )
    if highlights:
        source_metadata["plaud_highlights"] = highlights

    return RecordingJob(
        id=file_id,
        recorded_at=recorded_at,
        filename=file_detail["name"],
        source="plaud-mcp",
        transcript_data=transcript_data,
        duration_ms=duration,
        source_metadata=source_metadata,
    )
