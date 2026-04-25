from __future__ import annotations

import base64
import gzip
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


@dataclass
class PlaudRecording:
    id: str
    filename: str
    fullname: str
    duration_ms: int
    start_time: int
    end_time: int
    is_trash: bool
    filesize: int
    serial_number: str

    @classmethod
    def from_api_response(cls, data: dict) -> PlaudRecording:
        return cls(
            id=data["id"],
            filename=data["filename"],
            fullname=data["fullname"],
            duration_ms=data.get("duration", 0),
            start_time=data.get("start_time", 0),
            end_time=data.get("end_time", 0),
            is_trash=data.get("is_trash", False),
            filesize=data.get("filesize", 0),
            serial_number=data.get("serial_number", ""),
        )


class PlaudClient:
    def __init__(self, token: str, base_url: str = "https://api-euc1.plaud.ai"):
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "authorization": f"bearer {token}",
            "app-platform": "web",
        }

    async def list_recordings(self, include_trash: bool = False) -> list[PlaudRecording]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self.base_url}/file/simple/web",
                headers=self.headers,
                params={
                    "skip": 0,
                    "limit": 99999,
                    "is_trash": 2,
                    "sort_by": "start_time",
                    "is_desc": "true",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != 0:
            raise RuntimeError(f"Plaud API error: {data.get('msg', 'unknown')}")

        files = data.get("data_file_list", [])
        recordings = [PlaudRecording.from_api_response(f) for f in files]

        if not include_trash:
            recordings = [r for r in recordings if not r.is_trash]

        return recordings

    async def get_download_url(self, file_id: str) -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self.base_url}/file/temp-url/{file_id}",
                headers=self.headers,
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != 0:
            raise RuntimeError(f"Plaud API error getting download URL: {data}")

        url = data.get("temp_url")
        if not url:
            raise RuntimeError(f"No temp_url in response for {file_id}")

        return url

    async def download_audio(self, file_id: str, output_path: Path) -> Path:
        """Download audio file using streaming to avoid loading into memory."""
        url = await self.get_download_url(file_id)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with output_path.open("wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)
        return output_path

    async def get_user_info(self) -> dict:
        """
        GET /user/me - returns user info including id_hash for WebSocket.

        Response structure: id_hash is at response['data_user']['id_hash'].
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self.base_url}/user/me",
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_transcript(self, file_id: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self.base_url}/file/detail/{file_id}",
                headers=self.headers,
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != 0:
            raise RuntimeError(f"Plaud API error: {data.get('msg', 'unknown')}")

        content_list = data.get("data", {}).get("content_list", [])
        transcript_item = None
        for item in content_list:
            if item.get("data_type") == "transaction":
                transcript_item = item
                break

        if transcript_item is None:
            raise RuntimeError(f"Recording {file_id} not transcribed (no transcript in content_list)")

        data_link = transcript_item["data_link"]

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(data_link)
            resp.raise_for_status()
            raw = resp.content

        try:
            decompressed = gzip.decompress(raw)
        except gzip.BadGzipFile:
            decompressed = raw

        return json.loads(decompressed)

    def check_token_expiry(self) -> tuple[bool, int, str | None]:
        """
        Decode JWT exp claim, return (is_valid, days_remaining, error_message).

        Returns:
            - (True, days_remaining, None) if token is valid
            - (False, 0, error_message) if token is expired or invalid
        """
        try:
            parts = self.token.split(".")
            if len(parts) != 3:
                return False, 0, "Invalid JWT format (expected 3 parts)"

            payload = parts[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += "=" * padding

            decoded = base64.urlsafe_b64decode(payload)
            claims = json.loads(decoded)

            exp = claims.get("exp")
            if not exp:
                return False, 0, "JWT missing exp claim"

            import time
            now = int(time.time())
            remaining_seconds = exp - now

            if remaining_seconds <= 0:
                return False, 0, "Token expired"

            days_remaining = remaining_seconds // 86400
            return True, days_remaining, None

        except Exception as e:
            log.warning("Failed to decode JWT: %s", e)
            return False, 0, f"Failed to decode JWT: {e}"
