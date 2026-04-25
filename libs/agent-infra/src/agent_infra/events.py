from __future__ import annotations
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TraceEvent:
    """Typed event emitted during streaming agent execution."""
    kind: Literal["tool_start", "tool_result", "text", "complete", "error"]
    tool_name: str | None = None
    tool_input: dict | None = None
    content: str | None = None
    files_written: list[str] | None = None
    turns_used: int | None = None
    cost_usd: float | None = None
