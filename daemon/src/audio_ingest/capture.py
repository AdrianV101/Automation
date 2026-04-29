"""Agent SDK-powered session capture.

After a successful extraction, optionally run a second short SDK pass that
appends a devlog entry referencing the notes just written. Mirrors the shape
of pkm-session-end Step 2 + 3a: query vault_activity for what was just
written, then vault_append a structured entry to the project devlog.

Append-only: tools allowlist is TOOLS_CAPTURE, which is read-side plus
vault_append and vault_add_links. No vault_write, no vault_edit, no
vault_update_frontmatter, no admin tools.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from agent_infra import build_agent_options, run_agent_loop_streaming
from telegram_interface import BotConfig, TelegramStreamSender

from .extraction import AgentRoutingResult
from .prompts import AUTOMATION_DEVLOG_PATH, CAPTURE_SYSTEM_PROMPT
from .tools import TOOLS_CAPTURE

log = logging.getLogger(__name__)


USER_PROMPT_TEMPLATE = f"""\
The extraction agent just finished routing a voice recording. Capture a
devlog entry referencing the work.

**Transcript:** [[{{transcript_link}}]]
**One-line summary:** {{summary_line}}
**Files written by the extraction pass:**
{{files_block}}

Confirm the writes via vault_activity, compose a devlog entry following the
shape in your system prompt, and append it to {AUTOMATION_DEVLOG_PATH}
under the existing sessions heading.
"""


@dataclass
class CaptureResult:
    success: bool
    summary: str
    files_appended: list[str] = field(default_factory=list)
    error: str | None = None
    turns_used: int = 0

    def __post_init__(self) -> None:
        # Mirror AgentRoutingResult's success/error pair invariant.
        if self.success and self.error is not None:
            raise ValueError(
                "CaptureResult(success=True) must have error=None; "
                f"got error={self.error!r}"
            )
        if not self.success and self.error is None:
            raise ValueError(
                "CaptureResult(success=False) requires a non-None error"
            )


def _build_user_prompt(
    routing_result: AgentRoutingResult,
    transcript_path: Path,
) -> str:
    summary_line = (routing_result.summary or "").strip().splitlines()
    summary_first = summary_line[0] if summary_line else "(no summary)"

    files_block = "\n".join(f"- {f}" for f in routing_result.files_written) or "- (none)"

    return USER_PROMPT_TEMPLATE.format(
        transcript_link=transcript_path.stem,
        summary_line=summary_first,
        files_block=files_block,
    )


async def agent_capture_session(
    routing_result: AgentRoutingResult,
    transcript_path: Path,
    pkm_vault_path: Path,
    tg: BotConfig | None = None,
    thread_id: int | None = None,
) -> CaptureResult:
    """Append a devlog entry capturing the just-completed extraction work.

    Append-only: this agent cannot create or restructure notes; only
    vault_append (existing devlog) and vault_add_links (optional backlinks)
    are allowed.
    """
    user_prompt = _build_user_prompt(routing_result, transcript_path)
    options = build_agent_options(
        CAPTURE_SYSTEM_PROMPT, pkm_vault_path, allowed_tools=TOOLS_CAPTURE,
        max_turns=15,
    )
    sender = TelegramStreamSender(tg, thread_id) if tg else None
    on_event = sender.handle if sender else None
    loop_result = await run_agent_loop_streaming(user_prompt, options, on_event=on_event)
    if sender:
        try:
            await sender.flush()
        except Exception:
            # Capture data is correct; only the Telegram trace tail is lost.
            log.warning("Final trace flush failed, devlog append unaffected", exc_info=True)

    summary = "\n".join(loop_result.text_parts).strip()

    if loop_result.error:
        error_msg = loop_result.error
        if loop_result.tool_errors:
            error_msg = f"{error_msg}; tool_errors={loop_result.tool_errors[-2:]}"
        return CaptureResult(
            success=False,
            summary=summary,
            error=error_msg,
            turns_used=loop_result.turns_used,
        )

    if not loop_result.files_written:
        # The agent ran without raising and without hitting max_turns, but
        # never appended. Most likely cause: a forbidden tool attempt that the
        # SDK rejected (TOOLS_CAPTURE excludes vault_write/edit/etc), or an
        # MCP error against the devlog path. Surface the rejection content if
        # we have it -- without this, the failure is indistinguishable from
        # "agent decided no devlog needed."
        detail = (
            f"; tool_errors={loop_result.tool_errors[-2:]}"
            if loop_result.tool_errors else ""
        )
        msg = f"Capture pass appended no devlog entry (turns={loop_result.turns_used}){detail}"
        log.info(msg)
        return CaptureResult(
            success=False,
            summary=summary,
            error=msg,
            turns_used=loop_result.turns_used,
        )

    log.info(
        "Capture pass complete: %d files touched, %d turns used",
        len(loop_result.files_written), loop_result.turns_used,
    )
    return CaptureResult(
        success=True,
        summary=summary,
        files_appended=loop_result.files_written,
        turns_used=loop_result.turns_used,
    )
