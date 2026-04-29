from __future__ import annotations

import json

import httpx
import pytest
import respx

from unittest.mock import AsyncMock, MagicMock, patch

from telegram_interface import BotConfig
from audio_ingest.notifications import (
    send_error,
    send_routing_summary,
    send_speaker_labeling_prompt,
    send_transcription_error,
)

_TG = BotConfig(bot_token="test-token", chat_id="123")
_API = "https://api.telegram.org/bottest-token"


class TestNotificationThreadId:
    """Verify thread_id passthrough on send_routing_summary, send_error,
    send_transcription_error, and send_speaker_labeling_prompt."""

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

    @pytest.mark.asyncio
    async def test_send_speaker_labeling_prompt_text_passes_thread_id(self):
        response_data = {"ok": True, "result": {"message_id": 789}}

        with patch("audio_ingest.notifications.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.json.return_value = response_data
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp

            await send_speaker_labeling_prompt(
                "c1", "rec.ogg", "Hello", _TG, thread_id=33,
            )

        call_kwargs = mock_client.post.call_args[1]
        body = call_kwargs["json"]
        assert body["message_thread_id"] == 33

    @pytest.mark.asyncio
    async def test_send_speaker_labeling_prompt_voice_passes_thread_id(self, tmp_path):
        response_data = {"ok": True, "result": {"message_id": 789}}
        clip = tmp_path / "clip.ogg"
        clip.write_bytes(b"fake ogg")

        with patch("audio_ingest.notifications.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.json.return_value = response_data
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp

            await send_speaker_labeling_prompt(
                "c1", "rec.ogg", "Hello", _TG,
                voice_clip_path=clip, thread_id=44,
            )

        call_kwargs = mock_client.post.call_args[1]
        # sendVoice uses form data, not JSON
        form_data = call_kwargs["data"]
        assert form_data["message_thread_id"] == "44"

    @pytest.mark.asyncio
    async def test_send_speaker_labeling_prompt_omits_thread_id_when_none(self):
        response_data = {"ok": True, "result": {"message_id": 789}}

        with patch("audio_ingest.notifications.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.json.return_value = response_data
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp

            await send_speaker_labeling_prompt(
                "c1", "rec.ogg", "Hello", _TG,
            )

        call_kwargs = mock_client.post.call_args[1]
        body = call_kwargs["json"]
        assert "message_thread_id" not in body


class TestRoutingSummaryDedupFooter:
    """The Telegram routing-summary message exposes dedup-skip metrics so
    the user can audit per-recording how often the agent appended/edited an
    existing note instead of creating a new one. The footer is suppressed
    when the agent did no aux work (the typical case)."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_includes_dedup_footer_when_aux_work_done(self):
        from audio_ingest.extraction import AgentRoutingResult

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
        from audio_ingest.extraction import AgentRoutingResult

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
