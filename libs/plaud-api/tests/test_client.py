import base64
import gzip
import json
import time
from pathlib import Path

import httpx
import pytest
import respx

from plaud_api.client import PlaudClient, PlaudRecording

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_file_list_response() -> dict:
    return {
        "status": 0,
        "msg": "success",
        "request_id": "",
        "data_file_total": 2,
        "data_file_list": [
            {
                "id": "abc123",
                "filename": "Weekly Sync",
                "fullname": "abc123.ogg",
                "filesize": 639488,
                "duration": 149000,
                "start_time": 1770071212000,
                "end_time": 1770071361000,
                "is_trash": False,
                "serial_number": "8810B30245871039",
            },
            {
                "id": "def456",
                "filename": "Trashed Recording",
                "fullname": "def456.ogg",
                "filesize": 123456,
                "duration": 60000,
                "start_time": 1770000000000,
                "end_time": 1770000060000,
                "is_trash": True,
                "serial_number": "8810B30245871039",
            },
        ],
    }


@pytest.fixture
def sample_temp_url_response() -> dict:
    return {
        "status": 0,
        "temp_url": "https://s3.example.com/audio.ogg?signed=xyz",
        "temp_url_opus": None,
    }


def make_jwt(exp_offset_days: int = 30) -> str:
    exp = int(time.time()) + (exp_offset_days * 86400)
    payload = {"sub": "user123", "exp": exp}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{payload_b64}.signature"


class TestPlaudRecording:
    def test_from_api_response(self):
        data = {
            "id": "abc123",
            "filename": "Test Recording",
            "fullname": "abc123.ogg",
            "filesize": 100000,
            "duration": 60000,
            "start_time": 1770000000000,
            "end_time": 1770000060000,
            "is_trash": False,
            "serial_number": "DEVICE123",
        }
        recording = PlaudRecording.from_api_response(data)

        assert recording.id == "abc123"
        assert recording.filename == "Test Recording"
        assert recording.duration_ms == 60000
        assert recording.is_trash is False


class TestPlaudClient:
    @respx.mock
    @pytest.mark.asyncio
    async def test_list_recordings(self, sample_file_list_response):
        client = PlaudClient("fake_token", "https://api.plaud.test")

        respx.get("https://api.plaud.test/file/simple/web").mock(
            return_value=httpx.Response(200, json=sample_file_list_response)
        )

        recordings = await client.list_recordings(include_trash=False)

        assert len(recordings) == 1
        assert recordings[0].id == "abc123"
        assert recordings[0].filename == "Weekly Sync"

    @respx.mock
    @pytest.mark.asyncio
    async def test_list_recordings_include_trash(self, sample_file_list_response):
        client = PlaudClient("fake_token", "https://api.plaud.test")

        respx.get("https://api.plaud.test/file/simple/web").mock(
            return_value=httpx.Response(200, json=sample_file_list_response)
        )

        recordings = await client.list_recordings(include_trash=True)

        assert len(recordings) == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_download_url(self, sample_temp_url_response):
        client = PlaudClient("fake_token", "https://api.plaud.test")

        respx.get("https://api.plaud.test/file/temp-url/abc123").mock(
            return_value=httpx.Response(200, json=sample_temp_url_response)
        )

        url = await client.get_download_url("abc123")

        assert url == "https://s3.example.com/audio.ogg?signed=xyz"

    @respx.mock
    @pytest.mark.asyncio
    async def test_download_audio(self, tmp_path, sample_temp_url_response):
        client = PlaudClient("fake_token", "https://api.plaud.test")

        respx.get("https://api.plaud.test/file/temp-url/abc123").mock(
            return_value=httpx.Response(200, json=sample_temp_url_response)
        )
        respx.get("https://s3.example.com/audio.ogg?signed=xyz").mock(
            return_value=httpx.Response(200, content=b"fake audio content")
        )

        output_path = tmp_path / "test.ogg"
        result = await client.download_audio("abc123", output_path)

        assert result == output_path
        assert output_path.read_bytes() == b"fake audio content"

    def test_check_token_expiry_valid(self):
        token = make_jwt(exp_offset_days=100)
        client = PlaudClient(token)

        is_valid, days, error = client.check_token_expiry()

        assert is_valid is True
        assert 99 <= days <= 100
        assert error is None

    def test_check_token_expiry_expired(self):
        token = make_jwt(exp_offset_days=-1)
        client = PlaudClient(token)

        is_valid, days, error = client.check_token_expiry()

        assert is_valid is False
        assert days == 0
        assert error == "Token expired"

    def test_check_token_expiry_invalid_format(self):
        client = PlaudClient("not_a_valid_jwt")

        is_valid, days, error = client.check_token_expiry()

        assert is_valid is False
        assert days == 0
        assert "Invalid JWT format" in error

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_user_info(self):
        client = PlaudClient("fake_token", "https://api.plaud.test")

        # Match actual API structure: id_hash is nested inside data_user
        user_response = {
            "status": 0,
            "data_user": {
                "id": "user123",
                "id_hash": "abc123def456",
                "email": "test@example.com",
                "nickname": "Test User",
            },
        }

        respx.get("https://api.plaud.test/user/me").mock(
            return_value=httpx.Response(200, json=user_response)
        )

        result = await client.get_user_info()

        assert result["data_user"]["id_hash"] == "abc123def456"
        assert result["data_user"]["email"] == "test@example.com"

    @respx.mock
    @pytest.mark.asyncio
    async def test_list_recordings_raises_on_api_error(self):
        client = PlaudClient("fake_token", "https://api.plaud.test")

        error_response = {
            "status": 1,
            "msg": "Invalid token",
        }

        respx.get("https://api.plaud.test/file/simple/web").mock(
            return_value=httpx.Response(200, json=error_response)
        )

        with pytest.raises(RuntimeError, match="Plaud API error: Invalid token"):
            await client.list_recordings()

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_download_url_raises_on_api_error(self):
        client = PlaudClient("fake_token", "https://api.plaud.test")

        error_response = {
            "status": 1,
            "msg": "File not found",
        }

        respx.get("https://api.plaud.test/file/temp-url/abc123").mock(
            return_value=httpx.Response(200, json=error_response)
        )

        with pytest.raises(RuntimeError, match="Plaud API error getting download URL"):
            await client.get_download_url("abc123")

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_download_url_raises_on_missing_temp_url(self):
        client = PlaudClient("fake_token", "https://api.plaud.test")

        # Status is OK but no temp_url
        response = {
            "status": 0,
        }

        respx.get("https://api.plaud.test/file/temp-url/abc123").mock(
            return_value=httpx.Response(200, json=response)
        )

        with pytest.raises(RuntimeError, match="No temp_url in response"):
            await client.get_download_url("abc123")


class TestGetTranscript:
    @pytest.fixture
    def file_detail_response(self) -> dict:
        with open(FIXTURES_DIR / "plaud_file_detail_response.json") as f:
            return json.load(f)

    @pytest.fixture
    def transcript_segments(self) -> list[dict]:
        with open(FIXTURES_DIR / "plaud_transcript_response.json") as f:
            return json.load(f)

    @respx.mock
    @pytest.mark.asyncio
    async def test_happy_path(self, file_detail_response, transcript_segments):
        client = PlaudClient("fake_token", "https://api.plaud.test")

        respx.get("https://api.plaud.test/file/detail/abc123").mock(
            return_value=httpx.Response(200, json=file_detail_response)
        )

        transcript_bytes = json.dumps(transcript_segments).encode()
        data_link = file_detail_response["data"]["content_list"][0]["data_link"]
        respx.get(data_link).mock(
            return_value=httpx.Response(200, content=transcript_bytes)
        )

        result = await client.get_transcript("abc123")

        assert isinstance(result, list)
        assert len(result) == len(transcript_segments)
        assert result[0]["speaker"] == "Speaker 1"
        assert result[0]["content"] == transcript_segments[0]["content"]

        # S3 download must NOT include auth headers
        s3_route = respx.routes[1]
        s3_request = s3_route.calls[0].request
        assert "authorization" not in s3_request.headers

    @respx.mock
    @pytest.mark.asyncio
    async def test_api_error_raises(self):
        client = PlaudClient("fake_token", "https://api.plaud.test")

        respx.get("https://api.plaud.test/file/detail/abc123").mock(
            return_value=httpx.Response(200, json={"status": 1, "msg": "Not found"})
        )

        with pytest.raises(RuntimeError, match="Plaud API error"):
            await client.get_transcript("abc123")

    @respx.mock
    @pytest.mark.asyncio
    async def test_no_transcript_raises(self):
        client = PlaudClient("fake_token", "https://api.plaud.test")

        response = {
            "status": 0,
            "data": {
                "content_list": [
                    {"data_type": "outline", "data_link": "https://example.com/outline.json"}
                ]
            },
        }

        respx.get("https://api.plaud.test/file/detail/abc123").mock(
            return_value=httpx.Response(200, json=response)
        )

        with pytest.raises(RuntimeError, match="not transcribed"):
            await client.get_transcript("abc123")

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_content_list_raises(self):
        client = PlaudClient("fake_token", "https://api.plaud.test")

        response = {
            "status": 0,
            "data": {"content_list": []},
        }

        respx.get("https://api.plaud.test/file/detail/abc123").mock(
            return_value=httpx.Response(200, json=response)
        )

        with pytest.raises(RuntimeError, match="not transcribed"):
            await client.get_transcript("abc123")

    @respx.mock
    @pytest.mark.asyncio
    async def test_gzipped_response(self, file_detail_response, transcript_segments):
        client = PlaudClient("fake_token", "https://api.plaud.test")

        respx.get("https://api.plaud.test/file/detail/abc123").mock(
            return_value=httpx.Response(200, json=file_detail_response)
        )

        gzipped = gzip.compress(json.dumps(transcript_segments).encode())
        data_link = file_detail_response["data"]["content_list"][0]["data_link"]
        respx.get(data_link).mock(
            return_value=httpx.Response(200, content=gzipped)
        )

        result = await client.get_transcript("abc123")

        assert len(result) == len(transcript_segments)
        assert result[0]["speaker"] == "Speaker 1"

    @respx.mock
    @pytest.mark.asyncio
    async def test_non_gzipped_response(self, file_detail_response, transcript_segments):
        client = PlaudClient("fake_token", "https://api.plaud.test")

        respx.get("https://api.plaud.test/file/detail/abc123").mock(
            return_value=httpx.Response(200, json=file_detail_response)
        )

        raw_bytes = json.dumps(transcript_segments).encode()
        data_link = file_detail_response["data"]["content_list"][0]["data_link"]
        respx.get(data_link).mock(
            return_value=httpx.Response(200, content=raw_bytes)
        )

        result = await client.get_transcript("abc123")

        assert len(result) == len(transcript_segments)
        assert result[-1]["speaker"] == "Speaker 1"
