"""Plaud-specific email to pipeline adapter.

Lives in the daemon (not libs/email-ingest/) because it depends on
Plaud-specific attachment naming + speaker-turn text format.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path

from email_ingest import MalformedEmailError, ParsedEmail
from pkm import TranscriptData, TranscriptSegment

from .models import RecordingJob

log = logging.getLogger(__name__)


class MalformedPlaudEmailError(MalformedEmailError):
    pass


@dataclass(frozen=True)
class Infographic:
    content: bytes
    filename: str


@dataclass(frozen=True)
class PlaudEmailPayload:
    message_id: str
    date_header: str
    subject: str
    transcript_text: str
    summaries: dict[str, str] = field(default_factory=dict)
    infographic: Infographic | None = None


_SUMMARY_RE = re.compile(r"^summary-(.+)\.txt$", re.IGNORECASE)
_IMAGE_RE = re.compile(r"^image_[A-Za-z0-9]+\.jpg$", re.IGNORECASE)


def _from_domain(headers: dict[str, str]) -> str | None:
    from_header = headers.get("From", "")
    m = re.search(r"<[^@>]+@([^>]+)>", from_header)
    if m:
        return m.group(1).lower()
    m = re.search(r"@([^\s>]+)", from_header)
    return m.group(1).lower() if m else None


def parse_plaud_email(parsed: ParsedEmail) -> PlaudEmailPayload | None:
    # Two-stage: returns None for not-for-us mail (wrong subject or From);
    # raises MalformedPlaudEmailError for for-us-but-broken mail.
    subject = parsed.headers.get("Subject", "")
    from_domain = _from_domain(parsed.headers)

    if "[Plaud-AutoFlow]" not in subject:
        log.debug("Dropping email without Plaud subject: %s", subject)
        return None
    if from_domain != "plaud.ai":
        log.debug("Dropping email with non-Plaud From domain: %s", from_domain)
        return None

    transcript_att = None
    summaries: dict[str, str] = {}
    infographic: Infographic | None = None

    for att in parsed.attachments:
        name = att.filename
        if not name:
            continue
        if name == "transcript.txt":
            transcript_att = att
            continue
        m = _SUMMARY_RE.match(name)
        if m:
            key = m.group(1).strip()
            summaries[key] = att.content.decode("utf-8", errors="replace")
            continue
        if _IMAGE_RE.match(name):
            infographic = Infographic(content=att.content, filename=name)
            continue

    if transcript_att is None:
        raise MalformedPlaudEmailError(
            f"Plaud email {parsed.message_id} has no transcript.txt attachment",
        )

    return PlaudEmailPayload(
        message_id=parsed.message_id,
        date_header=parsed.headers.get("Date", ""),
        subject=subject,
        transcript_text=transcript_att.content.decode("utf-8", errors="replace"),
        summaries=summaries,
        infographic=infographic,
    )


_TURN_HEADER = re.compile(
    r"^(?P<speaker>.+?)\s+(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})\s*$",
    re.MULTILINE,
)


def _seconds(h: int, m: int, s: int) -> float:
    return float(h * 3600 + m * 60 + s)


def plaud_email_to_transcript_data(
    payload: PlaudEmailPayload,
    job_id: str,
    recorded_at: str,
) -> TranscriptData:
    # Each turn in Plaud's transcript:
    #   <speaker> HH:MM:SS\n<prose>\n\n
    # Segment start is the turn's timestamp; end is the next turn's
    # timestamp (or the same as start for the final turn, since Plaud
    # doesn't emit an explicit turn end).
    text = payload.transcript_text or ""

    headers: list[tuple[str, float, int]] = []
    for m in _TURN_HEADER.finditer(text):
        speaker = m.group("speaker").strip()
        secs = _seconds(int(m.group("h")), int(m.group("m")), int(m.group("s")))
        headers.append((speaker, secs, m.end()))

    if not headers:
        return TranscriptData(
            job_id=job_id,
            recorded_at=recorded_at,
            duration_seconds=0.0,
            speakers=["Speaker 1"],
            segments=[TranscriptSegment(start=0.0, end=0.0, speaker="Speaker 1", text=text.strip())],
            full_text=text.strip(),
        )

    segments: list[TranscriptSegment] = []
    speakers_seen: list[str] = []
    for i, (speaker, start, match_end) in enumerate(headers):
        if i + 1 < len(headers):
            next_match = _TURN_HEADER.search(text, match_end)
            next_start_char = next_match.start() if next_match else len(text)
            end = headers[i + 1][1]
        else:
            next_start_char = len(text)
            end = start
        body = text[match_end:next_start_char].strip()
        segments.append(TranscriptSegment(start=start, end=end, speaker=speaker, text=body))
        if speaker not in speakers_seen:
            speakers_seen.append(speaker)

    duration = headers[-1][1]
    full_text = "\n\n".join(s.text for s in segments if s.text)

    return TranscriptData(
        job_id=job_id,
        recorded_at=recorded_at,
        duration_seconds=duration,
        speakers=speakers_seen,
        segments=segments,
        full_text=full_text,
    )


@dataclass(frozen=True)
class SavedArtifacts:
    infographic_path: Path | None = None  # vault-relative


_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._@-]")


def _sanitize_message_id_for_filename(message_id: str) -> str:
    stripped = message_id.strip("<>").strip()
    return _SAFE_CHARS.sub("_", stripped) or "unknown"


def save_plaud_attachments(
    payload: PlaudEmailPayload,
    vault_path: Path,
    attachments_subdir: str,
) -> SavedArtifacts:
    # Non-fatal: JPEG write failures log WARN and return None so the
    # pipeline can continue without the infographic.
    if payload.infographic is None:
        return SavedArtifacts(infographic_path=None)

    subdir = vault_path / attachments_subdir
    fname = _sanitize_message_id_for_filename(payload.message_id) + ".jpg"
    target = subdir / fname

    if target.exists():
        return SavedArtifacts(infographic_path=Path(attachments_subdir) / fname)

    try:
        subdir.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload.infographic.content)
    except OSError:
        log.warning(
            "Failed to save infographic for %s -- continuing without it",
            payload.message_id, exc_info=True,
        )
        return SavedArtifacts(infographic_path=None)

    return SavedArtifacts(infographic_path=Path(attachments_subdir) / fname)


_PLAUD_IMG_PATTERN = re.compile(
    r"!\[PLAUD NOTE\]\(permanent/[^)]+?\.png\)",
    re.IGNORECASE,
)


def rewrite_plaud_summary_links(
    summaries: dict[str, str],
    infographic_vault_path: Path | None,
) -> dict[str, str]:
    # No-op when no infographic is available — preserves the broken link so
    # the extraction agent can decide how to handle it.
    if infographic_vault_path is None:
        return dict(summaries)
    replacement = f"![[{infographic_vault_path.as_posix()}]]"
    return {
        name: _PLAUD_IMG_PATTERN.sub(replacement, body)
        for name, body in summaries.items()
    }


def recording_job_from_email(
    parsed: ParsedEmail,
    vault_path: Path,
    attachments_subdir: str,
) -> RecordingJob | None:
    # Raises MalformedPlaudEmailError for for-us-but-broken mail.
    payload = parse_plaud_email(parsed)
    if payload is None:
        return None

    artifacts = save_plaud_attachments(
        payload, vault_path=vault_path, attachments_subdir=attachments_subdir,
    )
    rewritten_summaries = rewrite_plaud_summary_links(
        payload.summaries, infographic_vault_path=artifacts.infographic_path,
    )

    try:
        recorded_dt = parsedate_to_datetime(payload.date_header)
        recorded_at = recorded_dt.isoformat()
    except (TypeError, ValueError):
        log.warning("Could not parse Date header %r", payload.date_header)
        recorded_at = ""

    transcript_data = plaud_email_to_transcript_data(
        payload, job_id=payload.message_id, recorded_at=recorded_at,
    )
    duration_ms = int((transcript_data.duration_seconds or 0) * 1000) or None

    source_metadata: dict[str, object] = {}
    if rewritten_summaries:
        source_metadata["plaud_summaries"] = rewritten_summaries
    if artifacts.infographic_path is not None:
        source_metadata["infographic_path"] = artifacts.infographic_path

    return RecordingJob(
        id=payload.message_id,
        recorded_at=recorded_at,
        filename=payload.subject[:60],
        source="plaud-email",
        transcript_data=transcript_data,
        duration_ms=duration_ms,
        source_metadata=source_metadata,
    )
