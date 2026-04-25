from __future__ import annotations

from pathlib import Path

from ..types import TranscriptData
from ..utils import parse_date


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def write_raw_transcript(
    transcript: TranscriptData,
    pkm_vault_path: Path,
    source: str = "plaud",
    tags: list[str] | None = None,
) -> Path:
    dt = parse_date(transcript.recorded_at)
    rel = (
        f"04-Archive/transcripts/{dt.strftime('%Y/%m')}"
        f"/{dt.strftime('%Y-%m-%d')}-{transcript.job_id}.md"
    )
    path = pkm_vault_path / rel

    if tags is None:
        tags = ["transcript", source, "auto-generated"]
    tags_str = ", ".join(tags)

    duration_min = ""
    if transcript.duration_seconds:
        duration_min = f"{transcript.duration_seconds / 60:.0f} minutes"

    speakers_str = ", ".join(transcript.speakers)
    if transcript.segments:
        segments_md = "\n".join(
            f"[{_fmt_time(s.start)}] **{s.speaker}:** {s.text}"
            for s in transcript.segments
        )
    else:
        segments_md = transcript.full_text

    content = f"""\
---
type: transcript
source: {source}
recorded_at: {transcript.recorded_at}
duration: {duration_min}
speakers: [{speakers_str}]
job_id: {transcript.job_id}
tags: [{tags_str}]
---

# Transcript - {dt.strftime('%Y-%m-%d')}

**Duration:** {duration_min}
**Speakers:** {speakers_str}

---

{segments_md}
"""

    _ensure_dir(path)
    path.write_text(content)
    return path
