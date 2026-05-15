from __future__ import annotations

import json

import httpx
import pytest
import respx

from telegram_interface import BotConfig
from automation_daemon.notifications import (
    send_error,
    send_routing_summary,
    send_transcription_error,
)

_TG = BotConfig(bot_token="test-token", chat_id="123")
_API = "https://api.telegram.org/bottest-token"


class TestNotificationThreadId:
    """Verify thread_id passthrough on send_routing_summary, send_error,
    and send_transcription_error."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_send_routing_summary_passes_thread_id(self):
        route = respx.post(f"{_API}/sendMessage").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "result": {"message_id": 1}},
            ),
        )

        await send_routing_summary(None, _TG, thread_id=42)

        payload = json.loads(route.calls[0].request.content)
        assert payload["message_thread_id"] == 42

    @respx.mock
    @pytest.mark.asyncio
    async def test_send_routing_summary_omits_thread_id_when_none(self):
        route = respx.post(f"{_API}/sendMessage").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "result": {"message_id": 1}},
            ),
        )

        await send_routing_summary(None, _TG)

        payload = json.loads(route.calls[0].request.content)
        assert "message_thread_id" not in payload

    @respx.mock
    @pytest.mark.asyncio
    async def test_send_error_passes_thread_id(self):
        route = respx.post(f"{_API}/sendMessage").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "result": {"message_id": 1}},
            ),
        )

        await send_error("job1", "boom", _TG, thread_id=55)

        payload = json.loads(route.calls[0].request.content)
        assert payload["message_thread_id"] == 55

    @respx.mock
    @pytest.mark.asyncio
    async def test_send_transcription_error_passes_thread_id(self):
        route = respx.post(f"{_API}/sendMessage").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "result": {"message_id": 1}},
            ),
        )

        await send_transcription_error("rec.ogg", "GPU OOM", _TG, thread_id=77)

        payload = json.loads(route.calls[0].request.content)
        assert payload["message_thread_id"] == 77


class TestRoutingSummaryDedupFooter:
    """The Telegram routing-summary message exposes dedup-skip metrics so
    the user can audit per-recording how often the agent appended/edited an
    existing note instead of creating a new one. The footer is suppressed
    when the agent did no aux work (the typical case)."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_includes_dedup_footer_when_aux_work_done(self):
        from automation_daemon.extraction import AgentRoutingResult

        route = respx.post(f"{_API}/sendMessage").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "result": {"message_id": 1}},
            ),
        )
        result = AgentRoutingResult(
            success=True,
            summary="routed roadmap discussion",
            files_written=["00-Inbox/audio-ingestion/2026-04-29-roadmap.md"],
            summary_path="00-Inbox/audio-ingestion/2026-04-29-roadmap.md",
            turns_used=42,
            frontmatter_updated=["01-Projects/Automation/research/topic.md"],
            links_added=[
                "01-Projects/Automation/research/topic.md",
                "01-Projects/Automation/_index.md",
            ],
        )

        await send_routing_summary(result, _TG)

        payload = json.loads(route.calls[0].request.content)
        text = payload["text"]
        assert "Dedup-hit updates" in text
        assert "1 frontmatter" in text
        assert "2 backlinks" in text

    @respx.mock
    @pytest.mark.asyncio
    async def test_no_footer_when_no_aux_work(self):
        from automation_daemon.extraction import AgentRoutingResult

        route = respx.post(f"{_API}/sendMessage").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "result": {"message_id": 1}},
            ),
        )
        result = AgentRoutingResult(
            success=True, summary="routed", files_written=["00-Inbox/x.md"],
            turns_used=10,
        )

        await send_routing_summary(result, _TG)

        payload = json.loads(route.calls[0].request.content)
        assert "Dedup-hit updates" not in payload["text"]
