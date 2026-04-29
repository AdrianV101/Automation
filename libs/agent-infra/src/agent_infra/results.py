from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class AgentLoopResult:
    """Result from a single agent query loop."""
    text_parts: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    turns_used: int = 0
    error: str | None = None
    # Tool-call rejections / MCP errors observed during the loop. The SDK
    # delivers these as ToolResultBlock(is_error=True) and does NOT raise --
    # without this list, an agent that gives up after a rejection looks
    # identical to "agent did nothing." Truncated to last 4 to bound memory
    # if an agent loops on the same rejection.
    tool_errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SessionResponse:
    text: str
    files_written: list[str] = field(default_factory=list)
    session_id: str | None = None
    error: str | None = None
