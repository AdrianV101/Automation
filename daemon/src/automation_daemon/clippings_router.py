from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agent_infra import build_agent_options, run_agent_loop_streaming

from .clippings_state import parse_clipping
from .tools import TOOLS_CLIPPINGS, as_list

log = logging.getLogger(__name__)


@dataclass
class RouteOutcome:
    kind: Literal["routed", "needs_clarification", "failed"]
    routed_path: str | None = None
    links_added: int = 0
    plan_attached: str | None = None
    question: str | None = None
    candidates: list[str] = field(default_factory=list)
    error: str | None = None
    summary: str = ""
    turns_used: int = 0

    def __post_init__(self) -> None:
        if self.kind == "routed" and not self.routed_path:
            raise ValueError(
                "RouteOutcome(kind='routed') requires a non-empty routed_path"
            )
        if self.kind == "needs_clarification":
            if not self.question:
                raise ValueError(
                    "RouteOutcome(kind='needs_clarification') requires a question"
                )
            if not (2 <= len(self.candidates) <= 4):
                raise ValueError(
                    "RouteOutcome(kind='needs_clarification') requires 2-4 candidates"
                )
        if self.kind == "failed" and not self.error:
            raise ValueError(
                "RouteOutcome(kind='failed') requires a non-None error"
            )


def _last_sentinel_line(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        s = line.strip()
        if s.startswith("ROUTED |") or s.startswith("NEEDS_CLARIFICATION |"):
            return s
    return None


def parse_sentinel(text: str, *, summary: str = "", turns_used: int = 0) -> RouteOutcome:
    """Parse the agent's final machine-readable line into a RouteOutcome.

    A missing or malformed sentinel is `failed` — never silently a success.
    """
    line = _last_sentinel_line(text)
    if line is None:
        return RouteOutcome(
            kind="failed",
            error="agent produced no ROUTED/NEEDS_CLARIFICATION sentinel",
            summary=summary, turns_used=turns_used,
        )
    parts = [p.strip() for p in line.split("|")]
    try:
        if parts[0] == "ROUTED":
            if len(parts) != 4:
                raise ValueError("ROUTED requires 4 fields")
            links = int(parts[2].split(":", 1)[1])
            plan_raw = parts[3].split(":", 1)[1].strip()
            return RouteOutcome(
                kind="routed", routed_path=parts[1],
                links_added=links,
                plan_attached=None if plan_raw.lower() == "none" else plan_raw,
                summary=summary, turns_used=turns_used,
            )
        # NEEDS_CLARIFICATION
        if len(parts) != 3:
            raise ValueError("NEEDS_CLARIFICATION requires 3 fields")
        cand_raw = parts[2].split(":", 1)[1]
        candidates = [c.strip() for c in cand_raw.split(";") if c.strip()]
        if not (2 <= len(candidates) <= 4):
            raise ValueError("expected 2-4 candidates")
        return RouteOutcome(
            kind="needs_clarification", question=parts[1],
            candidates=candidates, summary=summary, turns_used=turns_used,
        )
    except (ValueError, IndexError) as exc:
        return RouteOutcome(
            kind="failed", error=f"malformed sentinel {line!r}: {exc}",
            summary=summary, turns_used=turns_used,
        )


_SYSTEM_PROMPT = (
    "You route a single web clipping into the Obsidian PKM. "
    "Follow the `clippings-router` skill exactly, including its sentinel "
    "output contract. Do not deviate from the skill's procedure."
)


def _build_user_prompt(
    frontmatter: dict, body: str, *, routing_targets: list[str],
    pinned_destination: str | None, user_guidance: str | None,
) -> str:
    lines = [
        "## Clipping frontmatter",
        f"title: {frontmatter.get('title')}",
        f"source: {frontmatter.get('source')}",
        f"description: {frontmatter.get('description')}",
        "",
        "## Clipping body",
        body.strip(),
        "",
        "## Routing targets (active project indexes and plans)",
        *(f"- {t}" for t in routing_targets),
    ]
    if pinned_destination:
        lines += ["", f"## Pinned destination (route here, do not re-deliberate)\n{pinned_destination}"]
    if user_guidance:
        lines += ["", f"## User guidance\n{user_guidance}"]
    return "\n".join(lines)


async def route_clipping(
    clipping_path: Path,
    vault_root: Path,
    *,
    routing_targets: list[str],
    pinned_destination: str | None = None,
    user_guidance: str | None = None,
    model: str = "claude-opus-4-7",
    max_turns: int = 60,
) -> RouteOutcome:
    """Run the routing agent for one clipping and return a parsed outcome.

    The agent follows the `clippings-router` skill and performs all vault
    mutations via Obsidian MCP. This function owns no side effects beyond
    the agent call; the adapter owns disposition/Telegram/state.
    """
    frontmatter, body = parse_clipping(clipping_path)
    options = build_agent_options(
        _SYSTEM_PROMPT,
        vault_root,
        model=model,
        allowed_tools=as_list(TOOLS_CLIPPINGS),
        max_turns=max_turns,
        allow_skill_tool=True,
        setting_sources=["project"],
    )
    user_prompt = _build_user_prompt(
        frontmatter, body, routing_targets=routing_targets,
        pinned_destination=pinned_destination, user_guidance=user_guidance,
    )
    loop_result = await run_agent_loop_streaming(user_prompt, options)
    summary = "\n".join(loop_result.text_parts).strip()
    if loop_result.error:
        err = loop_result.error
        if getattr(loop_result, "tool_errors", None):
            err = f"{err}; tool_errors={loop_result.tool_errors[-2:]}"
        return RouteOutcome(
            kind="failed", error=err, summary=summary,
            turns_used=loop_result.turns_used,
        )
    return parse_sentinel(summary, summary=summary, turns_used=loop_result.turns_used)
