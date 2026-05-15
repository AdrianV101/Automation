from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


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
