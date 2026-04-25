"""Tests for plaud_api.websocket — PlaudWebSocket class and run_websocket_loop()."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from plaud_api.client import PlaudRecording
from plaud_api.state import DownloadedRecording
from plaud_api.websocket import (
    RECONNECT_DELAY_INITIAL,
    RECONNECT_DELAY_MAX,
    PlaudWebSocket,
    _format_download_failures,
    _format_download_summary,
    run_websocket_loop,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_recording(
    id: str = "abc123",
    filename: str = "Test Recording",
    duration_ms: int = 120000,
) -> PlaudRecording:
    return PlaudRecording(
        id=id,
        filename=filename,
        fullname=f"{id}.ogg",
        duration_ms=duration_ms,
        start_time=1704067200000,
        end_time=1704067200000 + duration_ms,
        is_trash=False,
        filesize=100000,
        serial_number="DEVICE123",
    )


def _make_downloaded_recording(
    recording_id: str = "abc123",
    filename: str = "Test Recording",
    duration_ms: int = 120000,
) -> DownloadedRecording:
    return DownloadedRecording(
        recording_id=recording_id,
        filename=filename,
        recorded_at=1704067200000,
        duration_ms=duration_ms,
        downloaded_at="2024-01-01T00:00:00+00:00",
        local_path="n/a",
        filesize=100000,
    )


# ---------------------------------------------------------------------------
# PlaudWebSocket class tests (moved from daemon)
# ---------------------------------------------------------------------------


class TestPlaudWebSocket:
    def test_init_constructs_ws_url(self):
        ws = PlaudWebSocket(
            token="test-token",
            id_hash="abc123",
            base_url="https://api-euc1.plaud.ai",
        )
        assert ws.ws_url == "wss://api-euc1.plaud.ai/ws/notify?platform=web&hash_id=abc123"
        assert ws.token == "test-token"
        assert ws._reconnect_delay == RECONNECT_DELAY_INITIAL

    def test_init_handles_trailing_slash(self):
        ws = PlaudWebSocket(
            token="test-token",
            id_hash="abc123",
            base_url="https://api-euc1.plaud.ai/",
        )
        # Should still work (extra slash in path is fine)
        assert "wss://api-euc1.plaud.ai" in ws.ws_url

    @pytest.mark.asyncio
    async def test_listen_calls_event_handler(self):
        ws = PlaudWebSocket(
            token="test-token",
            id_hash="abc123",
            base_url="https://api-euc1.plaud.ai",
        )

        # Simulate receiving one message then disconnecting
        test_event = {"type": "file_sync", "file_id": "123"}
        events_received = []

        async def on_event(event: dict) -> None:
            events_received.append(event)

        # Create a mock that behaves like an async context manager
        class MockWebSocket:
            def __init__(self):
                self.messages = [json.dumps(test_event)]
                self.index = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.index < len(self.messages):
                    msg = self.messages[self.index]
                    self.index += 1
                    return msg
                raise StopAsyncIteration

        with patch.object(ws, "_connect", MockWebSocket):
            # Run listen in background, cancel after short delay
            task = asyncio.create_task(ws.listen(on_event))
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(events_received) == 1
        assert events_received[0] == test_event

    @pytest.mark.asyncio
    async def test_listen_handles_non_json_message(self):
        ws = PlaudWebSocket(
            token="test-token",
            id_hash="abc123",
            base_url="https://api-euc1.plaud.ai",
        )

        events_received = []

        async def on_event(event: dict) -> None:
            events_received.append(event)

        class MockWebSocket:
            def __init__(self):
                self.messages = ["not valid json"]
                self.index = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.index < len(self.messages):
                    msg = self.messages[self.index]
                    self.index += 1
                    return msg
                raise StopAsyncIteration

        with patch.object(ws, "_connect", MockWebSocket):
            task = asyncio.create_task(ws.listen(on_event))
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Non-JSON messages should be skipped, not crash
        assert len(events_received) == 0

    def test_reconnect_delay_bounds(self):
        ws = PlaudWebSocket(
            token="test-token",
            id_hash="abc123",
            base_url="https://api-euc1.plaud.ai",
        )

        # Initial delay
        assert ws._reconnect_delay == RECONNECT_DELAY_INITIAL

        # Simulate reconnect backoff
        ws._reconnect_delay = min(ws._reconnect_delay * 2, RECONNECT_DELAY_MAX)
        assert ws._reconnect_delay == 10

        # Keep doubling
        for _ in range(10):
            ws._reconnect_delay = min(ws._reconnect_delay * 2, RECONNECT_DELAY_MAX)

        # Should cap at max
        assert ws._reconnect_delay == RECONNECT_DELAY_MAX


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------


class TestFormatDownloadSummary:
    def test_single_recording(self):
        rec = _make_recording(duration_ms=120000)
        result = _format_download_summary([(rec, [{"speaker": "A"}])])
        assert "1 new recording(s)" in result
        assert "Test Recording (2 min)" in result
        assert "Total: 2 min" in result

    def test_multiple_recordings(self):
        rec1 = _make_recording(id="a", filename="Rec A", duration_ms=60000)
        rec2 = _make_recording(id="b", filename="Rec B", duration_ms=180000)
        result = _format_download_summary([
            (rec1, []),
            (rec2, []),
        ])
        assert "2 new recording(s)" in result
        assert "Rec A (1 min)" in result
        assert "Rec B (3 min)" in result
        assert "Total: 4 min" in result


class TestFormatDownloadFailures:
    def test_single_failure(self):
        rec = _make_recording()
        result = _format_download_failures([(rec, "Connection timeout")])
        assert "1 recording(s) failed" in result
        assert "Test Recording: Connection timeout" in result
        assert "Will retry" in result

    def test_multiple_failures(self):
        rec1 = _make_recording(id="a", filename="Rec A")
        rec2 = _make_recording(id="b", filename="Rec B")
        result = _format_download_failures([
            (rec1, "error 1"),
            (rec2, "error 2"),
        ])
        assert "2 recording(s) failed" in result
        assert "Rec A: error 1" in result
        assert "Rec B: error 2" in result


# ---------------------------------------------------------------------------
# run_websocket_loop tests
# ---------------------------------------------------------------------------


class TestRunWebsocketLoop:
    """Tests for the callback-driven run_websocket_loop()."""

    @pytest.fixture
    def mock_client(self):
        client = AsyncMock()
        client.token = "test-token"
        client.base_url = "https://api-euc1.plaud.ai"
        return client

    @pytest.fixture
    def mock_state_db(self):
        db = AsyncMock()
        db.get_pending_processing = AsyncMock(return_value=[])
        return db

    @pytest.fixture
    def on_new_recording(self):
        return AsyncMock()

    @pytest.fixture
    def on_status(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_startup_catchup_calls_on_new_recording(
        self, mock_client, mock_state_db, on_new_recording, on_status, tmp_path
    ):
        """When state_db.get_pending_processing() returns recordings,
        on_new_recording is called for each."""
        pending_rec = _make_downloaded_recording(recording_id="rec1")

        # download_new_recordings returns nothing new (already downloaded)
        # but get_pending_processing returns a pending recording
        mock_state_db.get_pending_processing.return_value = [pending_rec]

        with patch(
            "plaud_api.websocket.download_new_recordings",
            new_callable=AsyncMock,
            return_value=([], []),
        ), patch(
            "plaud_api.websocket.PlaudWebSocket.listen",
            new_callable=AsyncMock,
        ):
            await run_websocket_loop(
                mock_client,
                mock_state_db,
                id_hash="abc123",
                download_dir=tmp_path,
                on_new_recording=on_new_recording,
                on_status=on_status,
            )

        on_new_recording.assert_called_once_with(pending_rec)

    @pytest.mark.asyncio
    async def test_startup_catchup_multiple_pending(
        self, mock_client, mock_state_db, on_new_recording, on_status, tmp_path
    ):
        """Multiple pending recordings each trigger on_new_recording."""
        pending = [
            _make_downloaded_recording(recording_id="rec1", filename="Rec 1"),
            _make_downloaded_recording(recording_id="rec2", filename="Rec 2"),
        ]
        mock_state_db.get_pending_processing.return_value = pending

        with patch(
            "plaud_api.websocket.download_new_recordings",
            new_callable=AsyncMock,
            return_value=([], []),
        ), patch(
            "plaud_api.websocket.PlaudWebSocket.listen",
            new_callable=AsyncMock,
        ):
            await run_websocket_loop(
                mock_client,
                mock_state_db,
                id_hash="abc123",
                download_dir=tmp_path,
                on_new_recording=on_new_recording,
                on_status=on_status,
            )

        assert on_new_recording.call_count == 2
        on_new_recording.assert_any_call(pending[0])
        on_new_recording.assert_any_call(pending[1])

    @pytest.mark.asyncio
    async def test_new_download_via_websocket_event(
        self, mock_client, mock_state_db, on_new_recording, on_status, tmp_path
    ):
        """When a WebSocket event triggers download_new_recordings() returning
        new items, on_new_recording is called for each."""
        rec = _make_recording(id="new1", filename="New Recording")
        db_rec = _make_downloaded_recording(recording_id="new1", filename="New Recording")
        segments = [{"speaker": "A", "content": "Hello"}]

        # First call = startup (nothing new), second call = WS event (new recording)
        download_call_count = 0

        async def mock_download(*args, **kwargs):
            nonlocal download_call_count
            download_call_count += 1
            if download_call_count == 1:
                # Startup: nothing new
                return ([], [])
            else:
                # WS event: new recording downloaded
                return ([(rec, segments)], [])

        # get_pending_processing: first call = empty (startup), second call = has the new rec
        pending_call_count = 0

        async def mock_pending():
            nonlocal pending_call_count
            pending_call_count += 1
            if pending_call_count <= 1:
                return []
            else:
                return [db_rec]

        mock_state_db.get_pending_processing.side_effect = mock_pending

        # Capture the on_event handler from PlaudWebSocket.listen
        captured_handler = None

        async def mock_listen(on_event, **kwargs):
            nonlocal captured_handler
            captured_handler = on_event
            # Simulate one WebSocket event, then return
            await on_event({"type": "file_sync"})

        with patch(
            "plaud_api.websocket.download_new_recordings",
            side_effect=mock_download,
        ), patch(
            "plaud_api.websocket.PlaudWebSocket.listen",
            side_effect=mock_listen,
        ):
            await run_websocket_loop(
                mock_client,
                mock_state_db,
                id_hash="abc123",
                download_dir=tmp_path,
                on_new_recording=on_new_recording,
                on_status=on_status,
            )

        on_new_recording.assert_called_once_with(db_rec)

    @pytest.mark.asyncio
    async def test_status_callback_on_download(
        self, mock_client, mock_state_db, on_new_recording, on_status, tmp_path
    ):
        """When recordings are downloaded, on_status is called with a summary."""
        rec = _make_recording(id="rec1", filename="Meeting Notes", duration_ms=300000)
        segments = [{"speaker": "A", "content": "Hello"}]

        mock_state_db.get_pending_processing.return_value = []

        with patch(
            "plaud_api.websocket.download_new_recordings",
            new_callable=AsyncMock,
            return_value=([(rec, segments)], []),
        ), patch(
            "plaud_api.websocket.PlaudWebSocket.listen",
            new_callable=AsyncMock,
        ):
            await run_websocket_loop(
                mock_client,
                mock_state_db,
                id_hash="abc123",
                download_dir=tmp_path,
                on_new_recording=on_new_recording,
                on_status=on_status,
            )

        # on_status should have been called with the download summary
        on_status.assert_called_once()
        status_msg = on_status.call_args[0][0]
        assert "1 new recording(s)" in status_msg
        assert "Meeting Notes" in status_msg

    @pytest.mark.asyncio
    async def test_status_callback_on_failure(
        self, mock_client, mock_state_db, on_new_recording, on_status, tmp_path
    ):
        """When downloads fail, on_status is called with a failure message."""
        rec = _make_recording(id="rec1")

        mock_state_db.get_pending_processing.return_value = []

        with patch(
            "plaud_api.websocket.download_new_recordings",
            new_callable=AsyncMock,
            return_value=([], [(rec, "Connection timeout")]),
        ), patch(
            "plaud_api.websocket.PlaudWebSocket.listen",
            new_callable=AsyncMock,
        ):
            await run_websocket_loop(
                mock_client,
                mock_state_db,
                id_hash="abc123",
                download_dir=tmp_path,
                on_new_recording=on_new_recording,
                on_status=on_status,
            )

        on_status.assert_called_once()
        status_msg = on_status.call_args[0][0]
        assert "failed" in status_msg.lower()
        assert "Connection timeout" in status_msg

    @pytest.mark.asyncio
    async def test_no_callbacks_when_nothing_new(
        self, mock_client, mock_state_db, on_new_recording, on_status, tmp_path
    ):
        """When download_new_recordings() returns empty, no callbacks are invoked."""
        mock_state_db.get_pending_processing.return_value = []

        with patch(
            "plaud_api.websocket.download_new_recordings",
            new_callable=AsyncMock,
            return_value=([], []),
        ), patch(
            "plaud_api.websocket.PlaudWebSocket.listen",
            new_callable=AsyncMock,
        ):
            await run_websocket_loop(
                mock_client,
                mock_state_db,
                id_hash="abc123",
                download_dir=tmp_path,
                on_new_recording=on_new_recording,
                on_status=on_status,
            )

        on_new_recording.assert_not_called()
        on_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_runs_without_callbacks(
        self, mock_client, mock_state_db, tmp_path
    ):
        """run_websocket_loop works fine when no callbacks are provided."""
        rec = _make_recording()
        segments = [{"speaker": "A"}]
        mock_state_db.get_pending_processing.return_value = []

        with patch(
            "plaud_api.websocket.download_new_recordings",
            new_callable=AsyncMock,
            return_value=([(rec, segments)], []),
        ), patch(
            "plaud_api.websocket.PlaudWebSocket.listen",
            new_callable=AsyncMock,
        ):
            # Should not raise even without callbacks
            await run_websocket_loop(
                mock_client,
                mock_state_db,
                id_hash="abc123",
                download_dir=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_on_new_recording_error_does_not_stop_loop(
        self, mock_client, mock_state_db, on_status, tmp_path
    ):
        """If on_new_recording raises, the loop continues processing other recordings."""
        pending = [
            _make_downloaded_recording(recording_id="rec1", filename="Rec 1"),
            _make_downloaded_recording(recording_id="rec2", filename="Rec 2"),
        ]
        mock_state_db.get_pending_processing.return_value = pending

        call_count = 0

        async def flaky_callback(rec):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("callback failed")

        with patch(
            "plaud_api.websocket.download_new_recordings",
            new_callable=AsyncMock,
            return_value=([], []),
        ), patch(
            "plaud_api.websocket.PlaudWebSocket.listen",
            new_callable=AsyncMock,
        ):
            await run_websocket_loop(
                mock_client,
                mock_state_db,
                id_hash="abc123",
                download_dir=tmp_path,
                on_new_recording=flaky_callback,
                on_status=on_status,
            )

        # Both callbacks were attempted despite the first one failing
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_status_called_for_both_downloads_and_failures(
        self, mock_client, mock_state_db, on_new_recording, on_status, tmp_path
    ):
        """When both downloads and failures occur, on_status is called twice."""
        good_rec = _make_recording(id="good", filename="Good Rec")
        bad_rec = _make_recording(id="bad", filename="Bad Rec")
        segments = [{"speaker": "A"}]

        mock_state_db.get_pending_processing.return_value = []

        with patch(
            "plaud_api.websocket.download_new_recordings",
            new_callable=AsyncMock,
            return_value=([(good_rec, segments)], [(bad_rec, "Network error")]),
        ), patch(
            "plaud_api.websocket.PlaudWebSocket.listen",
            new_callable=AsyncMock,
        ):
            await run_websocket_loop(
                mock_client,
                mock_state_db,
                id_hash="abc123",
                download_dir=tmp_path,
                on_new_recording=on_new_recording,
                on_status=on_status,
            )

        assert on_status.call_count == 2
        calls = [call[0][0] for call in on_status.call_args_list]
        # First call should be the success summary
        assert "1 new recording(s)" in calls[0]
        # Second call should be the failure summary
        assert "failed" in calls[1].lower()
