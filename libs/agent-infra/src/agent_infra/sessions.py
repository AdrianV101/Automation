"""Transport-agnostic session lifecycle manager for ClaudeSDKClient.

Manages long-lived ClaudeSDKClient instances keyed by session_key.
Caches active clients in memory; stores session_id via a SessionStore
protocol for resume-on-restart.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from .events import TraceEvent
from .options import build_agent_options
from .results import SessionResponse
from .utils import extract_file_path
from .watchdog import AgentInactivityTimeout, with_inactivity_watchdog

log = logging.getLogger(__name__)

_MAX_SESSIONS = 50


@runtime_checkable
class SessionStore(Protocol):
    """Abstract storage for session_id persistence."""

    async def get_session_id(self, key: str) -> str | None: ...
    async def save_session_id(self, key: str, session_id: str) -> None: ...


class SessionManager:
    """Manage ClaudeSDKClient instances keyed by an abstract session_key."""

    def __init__(self, session_store: SessionStore, pkm_vault_path: Path):
        self._store = session_store
        self._pkm_vault_path = pkm_vault_path
        self._clients: dict[str, ClaudeSDKClient] = {}

    def _build_options(
        self, system_prompt: str, *,
        allowed_tools: list[str] | None = None,
        resume: str | None = None,
    ) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions, optionally configured for session resume."""
        opts = build_agent_options(
            system_prompt, self._pkm_vault_path,
            model="claude-sonnet-4-6", allowed_tools=allowed_tools,
        )
        if resume:
            opts.resume = resume
            opts.continue_conversation = True
        return opts

    async def send(
        self,
        session_key: str,
        message: str,
        system_prompt: str,
        on_event: Callable[[TraceEvent], Awaitable[None]] | None = None,
        allowed_tools: list[str] | None = None,
        inactivity_timeout_s: float | None = None,
    ) -> SessionResponse:
        """Send a message in a session. Creates or reuses a ClaudeSDKClient.

        If inactivity_timeout_s is set, the response collection is wrapped in
        an inactivity watchdog: if no TraceEvent arrives for that many seconds,
        the session is evicted and SessionResponse carries an error.
        """
        client = self._clients.get(session_key)

        if client is None:
            # Evict oldest session if at capacity
            if len(self._clients) >= _MAX_SESSIONS:
                oldest_key = next(iter(self._clients))
                await self.close_session(oldest_key)

            # Check if we can resume an existing session
            resume_id = await self._store.get_session_id(session_key)

            options = self._build_options(
                system_prompt, allowed_tools=allowed_tools, resume=resume_id,
            )
            client = ClaudeSDKClient(options=options)
            await client.__aenter__()
            self._clients[session_key] = client

        try:
            await client.query(message)
        except Exception:
            log.exception("Client query failed for session %s, evicting", session_key)
            await self.close_session(session_key)
            return SessionResponse(
                text="Session error. Please try again.",
                error="Client query failed",
            )

        if inactivity_timeout_s is None:
            return await self._collect_response(client, session_key, on_event=on_event)

        # Run with inactivity watchdog. The watchdog wraps the user's on_event
        # and will cancel _collect_response if no TraceEvent arrives in time.
        async def _work(emit):
            return await self._collect_response(client, session_key, on_event=emit)

        try:
            return await with_inactivity_watchdog(
                _work, on_event=on_event,
                inactivity_timeout_s=inactivity_timeout_s,
            )
        except AgentInactivityTimeout as exc:
            log.error("Session %s timed out: %s", session_key, exc)
            await self.close_session(session_key)
            return SessionResponse(
                text="Agent went silent — request cancelled.",
                error=f"inactivity timeout after {inactivity_timeout_s:.0f}s",
            )

    async def _collect_response(
        self, client: ClaudeSDKClient, session_key: str,
        on_event: Callable[[TraceEvent], Awaitable[None]] | None = None,
    ) -> SessionResponse:
        """Iterate over client.receive_response() and build a SessionResponse."""
        text_parts: list[str] = []
        files_written: list[str] = []
        session_id: str | None = None

        async def _emit(event: TraceEvent) -> None:
            if on_event is not None:
                try:
                    await on_event(event)
                except Exception:
                    log.warning("Trace callback failed for %s", event.kind, exc_info=True)

        try:
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                            await _emit(TraceEvent(kind="text", content=block.text))
                        elif isinstance(block, ToolUseBlock):
                            path = extract_file_path(block.name, block.input)
                            if path and path not in files_written:
                                files_written.append(path)
                            await _emit(TraceEvent(
                                kind="tool_start",
                                tool_name=block.name,
                                tool_input=block.input,
                            ))
                elif isinstance(msg, UserMessage):
                    for block in msg.content:
                        if isinstance(block, ToolResultBlock):
                            content = block.content if isinstance(block.content, str) else str(block.content)
                            await _emit(TraceEvent(kind="tool_result", content=content))
                elif isinstance(msg, ResultMessage):
                    session_id = getattr(msg, "session_id", None)
                    turns = getattr(msg, "num_turns", None)
                    cost = getattr(msg, "total_cost_usd", None)
                    await _emit(TraceEvent(
                        kind="complete",
                        turns_used=turns,
                        cost_usd=cost,
                        files_written=list(files_written),
                    ))
        except Exception:
            log.exception("Error collecting response for %s", session_key)
            await _emit(TraceEvent(kind="error", content="Error collecting session response"))
            return SessionResponse(
                text="\n".join(text_parts).strip() or "Session error. Please try again.",
                files_written=files_written,
                error="Partial response; error during collection",
            )

        if session_id:
            try:
                await self._store.save_session_id(session_key, session_id)
            except Exception:
                log.exception("Failed to persist session %s", session_key)

        return SessionResponse(
            text="\n".join(text_parts).strip() or "(no response)",
            files_written=files_written,
            session_id=session_id,
        )

    async def close_session(self, session_key: str) -> None:
        """Close and remove a cached client by session_key."""
        client = self._clients.pop(session_key, None)
        if client:
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                log.exception("Error closing session %s", session_key)

    async def close_all(self) -> None:
        """Close all cached clients."""
        for key in list(self._clients):
            await self.close_session(key)
