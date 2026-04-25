from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class AgentLoopResult:
    """Result from a single agent query loop."""
    text_parts: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    turns_used: int = 0
    error: str | None = None


@dataclass(frozen=True)
class SessionResponse:
    text: str
    files_written: list[str] = field(default_factory=list)
    session_id: str | None = None
    error: str | None = None
