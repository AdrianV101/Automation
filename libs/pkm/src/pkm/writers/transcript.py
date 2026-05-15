from __future__ import annotations

from pathlib import Path

from ..types import TranscriptData
from ..utils import parse_date


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


# Characters that, unquoted, would corrupt a YAML flow-sequence entry.
_YAML_UNSAFE = set(",:[]{}\"'#&*!|>%@`")


def _yaml_seq_item(name: str) -> str:
    """Render `name` as one YAML flow-sequence item: bare when safe,
    double-quoted (with escaping) when it contains YAML-significant
    characters or surrounding whitespace. Keeps normal names (e.g.
    'Speaker 1', 'Alice') byte-identical to the previous bare output."""
    if name and name == name.strip() and not (_YAML_UNSAFE & set(name)):
        return name
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_raw_transcript(
    transcript: TranscriptData,
    pkm_vault_path: Path,
    source: str = "plaud",
    tags: list[str] | None = None,
    speakers_unresolved: bool = False,
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
    speakers_fm = ", ".join(_yaml_seq_item(s) for s in transcript.speakers)
    unresolved_line = "speakers_unresolved: true\n" if speakers_unresolved else ""
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
speakers: [{speakers_fm}]
job_id: {transcript.job_id}
{unresolved_line}tags: [{tags_str}]
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
