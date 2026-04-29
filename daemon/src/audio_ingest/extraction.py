"""Agent SDK-powered extraction and PKM routing.

Replaces the old extraction.py + pkm_writer.write_summary_note() flow with a
Claude agent that has direct access to the Obsidian PKM via MCP tools.  The agent
reads the transcript, extracts information, searches the vault for context, and
routes extracted items to the appropriate locations autonomously.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_infra import build_agent_options, parse_date, run_agent_loop_streaming
from .prompts import build_extraction_system_prompt
from .tools import TOOLS_EXTRACTION
from telegram_interface import BotConfig
from pkm import TranscriptData
from telegram_interface import TelegramStreamSender

log = logging.getLogger(__name__)


USER_PROMPT_TEMPLATE = """\
Here is a voice recording transcript to process:

**Recording date:** {date}
**Duration:** {duration} minutes
**Speakers:** {speakers}
**Raw transcript link:** [[{transcript_link}]]

---

{full_text}

---

Please extract all information and route it to the appropriate PKM locations \
using the available vault tools. Write the inbox summary note FIRST (00-Inbox/audio-ingestion/), \
then search the vault for related context and append/create project-specific notes as needed. \
When done, provide a brief summary of what you routed and where.
"""


@dataclass
class AgentRoutingResult:
    success: bool
    summary: str
    files_written: list[str] = field(default_factory=list)
    summary_path: str | None = None
    error: str | None = None
    turns_used: int = 0
    # Auxiliary mutations: notes whose frontmatter was edited via
    # vault_update_frontmatter, and notes that received link additions via
    # vault_add_links. These are how the per-item write protocol's dedup-hit
    # branch (similarity > 0.8 -> append/edit/update_frontmatter on the
    # existing note + bidirectional link insertion) shows up in the result.
    # Surfaced so dedup-skip decisions can be audited from the daemon log.
    frontmatter_updated: list[str] = field(default_factory=list)
    links_added: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Enforce the success/error pair invariant. The gate protects the
        # consumers in pipeline.py / notifications.py / capture.py that read
        # `error` after seeing `success=False` (or that suppress error
        # messaging on `success=True`); without the gate, a misconstruction
        # like `success=True, error="oops"` would silently mislead operators.
        # Files-vs-success is intentionally NOT enforced here -- the pipeline
        # gate AND-conjoins success and files_written on purpose, defending
        # against a future invariant weakening; that defense is testable.
        if self.success and self.error is not None:
            raise ValueError(
                "AgentRoutingResult(success=True) must have error=None; "
                f"got error={self.error!r}"
            )
        if not self.success and self.error is None:
            raise ValueError(
                "AgentRoutingResult(success=False) requires a non-None error"
            )


def _build_user_prompt(
    transcript: TranscriptData,
    transcript_path: Path,
    source_metadata: Mapping[str, Any] | None = None,
) -> str:
    dt = parse_date(transcript.recorded_at)
    duration = f"{transcript.duration_seconds / 60:.0f}" if transcript.duration_seconds else "unknown"
    speakers = ", ".join(transcript.speakers) if transcript.speakers else "unknown"
    transcript_link = transcript_path.stem

    base = USER_PROMPT_TEMPLATE.format(
        date=dt.strftime("%Y-%m-%d"),
        duration=duration,
        speakers=speakers,
        transcript_link=transcript_link,
        full_text=transcript.full_text,
    )

    meta: Mapping[str, Any] = source_metadata or {}
    plaud_summaries = meta.get("plaud_summaries")
    infographic_path = meta.get("infographic_path")

    extras: list[str] = []
    if plaud_summaries:
        block = "\n\n".join(
            f"### Plaud summary — {name}\n{text}"
            for name, text in plaud_summaries.items()
        )
        extras.append(
            "Plaud has already produced the following summaries for this recording. "
            "Use them as hints to improve your extraction; you still own final "
            "routing and linking decisions.\n\n" + block,
        )
    if infographic_path is not None:
        extras.append(
            f"An infographic for this recording is saved at `{infographic_path}`. "
            "Include it as an embedded link in whatever note you create for this recording, "
            f"using Obsidian wikilink syntax: `![[{infographic_path}]]`.",
        )

    if extras:
        return base + "\n\n---\n\n" + "\n\n---\n\n".join(extras)
    return base


def _find_summary_path(files: list[str]) -> str | None:
    """Find the main inbox summary note from written files."""
    for f in files:
        if "00-Inbox/audio-ingestion/" in f:
            return f
    return None


async def agent_extract_and_route(
    transcript: TranscriptData,
    transcript_path: Path,
    pkm_vault_path: Path,
    tg: BotConfig | None = None,
    thread_id: int | None = None,
    source_metadata: Mapping[str, Any] | None = None,
) -> AgentRoutingResult:
    user_prompt = _build_user_prompt(
        transcript, transcript_path, source_metadata=source_metadata,
    )
    # Turn cap sized for the multi-stage prompt: Stage 0 dedup (~2) + Stage 1
    # context sweep across topics (~10) + always-on inbox summary (~5) +
    # per-item write protocol (~6 turns/item, typically 5-10 items) +
    # bidirectional linking on structured templates. A moderate transcript
    # lower-bounds at ~75 turns; 150 gives headroom without going unbounded.
    options = build_agent_options(
        build_extraction_system_prompt(pkm_vault_path),
        pkm_vault_path,
        allowed_tools=TOOLS_EXTRACTION,
        max_turns=150,
    )
    sender = TelegramStreamSender(tg, thread_id) if tg else None
    on_event = sender.handle if sender else None
    loop_result = await run_agent_loop_streaming(user_prompt, options, on_event=on_event)
    if sender:
        try:
            await sender.flush()
        except Exception:
            log.warning("Final trace flush failed, agent result unaffected", exc_info=True)

    summary = "\n".join(loop_result.text_parts).strip()
    summary_path = _find_summary_path(loop_result.files_written)

    if loop_result.error:
        error_msg = loop_result.error
        if loop_result.tool_errors:
            error_msg = f"{error_msg}; tool_errors={loop_result.tool_errors[-2:]}"
        # Preserve the partial trace text on failure -- it's the only
        # forensic evidence of what the agent was doing before it crashed
        # / hit max_turns. Mirrors capture.py's reconciled behaviour.
        return AgentRoutingResult(
            success=False,
            summary=summary,
            error=error_msg,
            turns_used=loop_result.turns_used,
            frontmatter_updated=list(loop_result.frontmatter_updated),
            links_added=list(loop_result.links_added),
        )

    if not loop_result.files_written:
        # The always-on inbox summary should always be in files_written --
        # the prompt excludes it from the dedup gate. Empty files_written
        # therefore means even the inbox write didn't happen, regardless of
        # whether aux mutations did. Surface tool_errors so an operator can
        # distinguish "agent gave up after MCP rejection" from "agent saw
        # zero routable items and silently violated protocol."
        detail = (
            f"; tool_errors={loop_result.tool_errors[-2:]}"
            if loop_result.tool_errors else ""
        )
        if loop_result.frontmatter_updated or loop_result.links_added:
            detail += (
                f"; frontmatter_updated={len(loop_result.frontmatter_updated)}"
                f", links_added={len(loop_result.links_added)}"
            )
        msg = f"Agent completed but wrote no files (turns={loop_result.turns_used}){detail}"
        log.warning(msg)
        return AgentRoutingResult(
            success=False,
            summary=summary,
            error=msg,
            turns_used=loop_result.turns_used,
            frontmatter_updated=list(loop_result.frontmatter_updated),
            links_added=list(loop_result.links_added),
        )

    log.info(
        "Agent routing complete: %d files written, %d frontmatter updated, "
        "%d links added, %d turns used",
        len(loop_result.files_written),
        len(loop_result.frontmatter_updated),
        len(loop_result.links_added),
        loop_result.turns_used,
    )
    return AgentRoutingResult(
        success=True,
        summary=summary,
        files_written=loop_result.files_written,
        summary_path=summary_path,
        turns_used=loop_result.turns_used,
        frontmatter_updated=list(loop_result.frontmatter_updated),
        links_added=list(loop_result.links_added),
    )
