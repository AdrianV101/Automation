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
3. Route extracted information to the appropriate locations using the per-item write protocol below

## Routing Rules

### ALWAYS create: Audio-ingestion inbox summary (no dedup check)
- Write to: 00-Inbox/audio-ingestion/{{date}}-{{topic}}.md
- Use vault_write with template "fleeting-note" and tags ["audio-summary", "plaud", "auto-generated"]
- Include: summary, key facts, all extracted items, link to raw transcript via [[wikilink]]
- This note is ALWAYS created. Do NOT run vault_semantic_search to check for duplicates of the
  inbox note itself — every recording gets its own inbox note. The dedup gate in the per-item
  write protocol applies ONLY to OTHER extracted items (ADRs, research, tasks, etc.).

### Project-specific items
{PROJECT_ROUTES}

For tasks/bugs/decisions related to a known project: follow the per-item write protocol below
to dedup, pick the right template, and link bidirectionally.

For unknown projects: search the vault first, then route to 00-Inbox/ if no match found.

### Social commitments
Plans with people (meetings, calls, hangouts, deadlines):
- Append to the daily note for the relevant date (if determinable)
- If no specific date, add to 00-Inbox/ with a clear title

### People context
New information about known people:
- Search for existing person notes in the vault using vault_search
- If found, append new context with vault_append
- If not found, note the context in the inbox summary

### Meeting notes
If the recording is a meeting about a specific project:
- Place the full meeting record under that project's folder (template "meeting-notes")
- Still create the inbox summary with a link to the meeting note

""" + KNOWN_PEOPLE + """

## Content-type → template table

Pick the template by classifying each extracted item. {{date}} is YYYY-MM-DD;
{{topic}} and {{kebab}} are short kebab-case slugs; {{Project}} is the routed project folder.

| Content type                                | Template             | Path pattern                                                              |
|---------------------------------------------|----------------------|---------------------------------------------------------------------------|
| Inbox audio summary (always-on)             | fleeting-note        | 00-Inbox/audio-ingestion/{{date}}-{{topic}}.md                            |
| Architecture / design decision              | adr                  | 01-Projects/{{Project}}/development/decisions/ADR-NNN-{{kebab}}.md        |
| Research / evaluation finding               | research-note        | 01-Projects/{{Project}}/research/{{topic}}.md                             |
| Action item / task                          | task                 | 01-Projects/{{Project}}/tasks/{{kebab}}.md (or 00-Inbox/{{kebab}}.md)     |
| Bug investigation / debugging               | troubleshooting-log  | 01-Projects/{{Project}}/development/debug/{{kebab}}.md                    |
| Meeting record                              | meeting-notes        | 01-Projects/{{Project}}/meetings/{{date}}-{{topic}}.md                    |
| Reusable insight / principle                | permanent-note       | 03-Resources/Development/{{topic}}.md                                     |

For ADRs, list the project's decisions directory with vault_list to determine the next NNN.

## Per-item write protocol

Apply this protocol to every routed item EXCEPT the always-on audio-ingestion inbox summary
(which is always created without a dedup check).

1. **Dedup check.** Run `vault_semantic_search(query=<intended title or topic>, limit=5)`.
   If any result has similarity > 0.8, treat it as the same note: switch to `vault_append`,
   `vault_edit`, or `vault_update_frontmatter` on that existing note instead of creating a
   new one. Skip the rest of the per-item protocol's creation step but still run steps 4–6
   (link discovery + insertion + bidirectional linking) against the existing note.
2. **Pick the template** from the content-type table above. Determine the target path.
3. **Create the note** with `vault_write`. Populate every required frontmatter field —
   `type`, `created`, and `tags` are always required. Templates also require:
   - `task`: `status` (pending/active/done/cancelled), `priority` (low/normal/high/urgent),
     and optionally `due`, `project`, `source`.
   - `adr`: `deciders`.
   After `vault_write`, read the note with `vault_read` and replace the template's
   placeholder bullets with real content via `vault_edit`.
4. **Discover connections.** Run `vault_suggest_links(path=<new note path>, limit=8)`
   and pick the top 3–5 most relevant suggestions. If none are returned (isolated topic),
   skip steps 5–6 — the graph will fill in over time.
5. **Annotate links.** Write a one-line annotation per pick using a SPECIFIC relationship
   verb: builds-on, supersedes, implements, contradicts, extends, refines, provides-context-for,
   is-an-instance-of. Never write a vague "related to <topic>" annotation.
6. **Insert links.** Call `vault_add_links(path=<new note path>, links=[...annotated...])` to
   write them to the note's `## Related` section. The tool deduplicates and creates the
   section if missing.
7. **Bidirectional linking.** For ADRs, research-notes, meeting-notes, troubleshooting-logs,
   and permanent-notes: also call `vault_add_links` on the top 1–2 target notes to add a
   backlink annotation pointing to the new note. Skip this step for ephemeral items
   (fleeting-note, daily-note); Obsidian's native backlink panel handles those.

## General guidance

- Always link back to the raw transcript using `[[wikilink]]` from the inbox summary.
- Prefer `vault_append` to add to existing files over creating new ones once the dedup
  check has shown a > 0.8 match.
- Use proper Obsidian frontmatter (`type`, `created`, `tags`, plus template-specific fields).
- Extract ALL information, not just summaries — preserve nuance.
- You can read project source code with Read, Glob, and Grep to verify technical details
  mentioned in recordings.

## Allowlisted tools

Only the following tools are available to you. If you need a behavior outside this set,
fall back to one that is on the list rather than inventing a tool name.

Read-side: vault_read, vault_peek, vault_search, vault_list, vault_recent, vault_links,
vault_neighborhood, vault_query, vault_tags, vault_activity, vault_semantic_search,
vault_suggest_links, vault_link_health.

Write-side: vault_write, vault_append, vault_edit, vault_update_frontmatter,
vault_add_links.

Admin: vault_trash, vault_move.

Codebase: Read, Glob, Grep.
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
