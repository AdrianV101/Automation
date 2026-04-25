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
from .tools import KNOWN_PEOPLE, TOOLS_EXTRACTION
from telegram_interface import BotConfig
from pkm import TranscriptData
from telegram_interface import TelegramStreamSender

try:
    from .user_context import USER_NAME, PROJECT_ROUTES
except ImportError:
    USER_NAME = "User"
    PROJECT_ROUTES = ""

log = logging.getLogger(__name__)

SYSTEM_PROMPT = f"""\
You are an information extraction and routing agent for {USER_NAME}'s Obsidian PKM.

Given a voice recording transcript, you must:
1. Extract ALL substantive information (facts, decisions, tasks, follow-ups, social plans, people context)
2. Search the vault for related existing notes, projects, and people
3. Route extracted information to the appropriate locations

## Routing Rules

### Always create: Summary note
- Write to: 00-Inbox/audio-ingestion/{{date}}-{{topic}}.md
- Use vault_write with template "fleeting-note" and tags ["audio-summary", "plaud", "auto-generated"]
- Include: summary, key facts, all extracted items, link to raw transcript

### Project-specific items
{PROJECT_ROUTES}

For tasks/bugs/decisions related to a known project:
- Search for the project's task list or devlog
- Append tasks using vault_append under the appropriate heading
- If no suitable file exists, create one in the project folder

For unknown projects: search the vault first, then route to 00-Inbox/ if no match found.

### Social commitments
Plans with people (meetings, calls, hangouts, deadlines):
- Append to the daily note for the relevant date (if determinable)
- If no specific date, add to 00-Inbox/ with a clear title

### People context
New information about known people:
- Search for existing person notes in the vault
- If found, append new context
- If not found, note the context in the summary

### Meeting notes
If the recording is a meeting about a specific project:
- Place the full summary under that project's folder (e.g., 01-Projects/{{Project}}/meetings/)
- Still create the inbox summary with a link

""" + KNOWN_PEOPLE + """

## Tools
- Use vault_search and vault_query to find existing context before writing
- You can read project source code with Read, Glob, and Grep to verify technical details mentioned in recordings

## Important
- Always link back to the raw transcript using [[wikilink]]
- Prefer vault_append to add to existing files over creating new ones
- Use proper Obsidian frontmatter (type, tags, etc.)
- Extract ALL information, not just summaries - preserve nuance
"""

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
using the available vault tools. Start by searching the vault for related context, \
then write/append as needed. When done, provide a brief summary of what you routed \
and where.
"""


@dataclass
class AgentRoutingResult:
    success: bool
    summary: str
    files_written: list[str] = field(default_factory=list)
    summary_path: str | None = None
    error: str | None = None
    turns_used: int = 0


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
    options = build_agent_options(SYSTEM_PROMPT, pkm_vault_path, allowed_tools=TOOLS_EXTRACTION)
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
        return AgentRoutingResult(
            success=False,
            summary="",
            error=loop_result.error,
            turns_used=loop_result.turns_used,
        )

    if not loop_result.files_written:
        log.warning("Agent completed but wrote no files (turns=%d)", loop_result.turns_used)
        return AgentRoutingResult(
            success=False,
            summary=summary,
            error="Agent completed but wrote no files",
            turns_used=loop_result.turns_used,
        )

    log.info(
        "Agent routing complete: %d files written, %d turns used",
        len(loop_result.files_written), loop_result.turns_used,
    )
    return AgentRoutingResult(
        success=True,
        summary=summary,
        files_written=loop_result.files_written,
        summary_path=summary_path,
        turns_used=loop_result.turns_used,
    )
