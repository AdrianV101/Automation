"""Async HTTP client for the public Hacker News Firebase API.

Free, no auth, no documented rate limits. Returns raw item dicts; the adapter
layer is responsible for filtering and converting to NewsItem.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

HN_API_BASE = "https://hacker-news.firebaseio.com"

log = logging.getLogger(__name__)


class HackerNewsClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def top_story_ids(self, limit: int) -> list[int]:
        resp = await self._http.get("/v0/topstories.json")
        resp.raise_for_status()
        ids = resp.json()
        if not isinstance(ids, list):
            raise ValueError(f"topstories.json returned non-list: {type(ids).__name__}")
        return ids[:limit]

    async def get_item(self, item_id: int) -> dict[str, Any] | None:
        resp = await self._http.get(f"/v0/item/{item_id}.json")
        if resp.status_code != 200:
            return None
        body = resp.json()
        if body is None:
            return None
        if not isinstance(body, dict):
            log.warning("HN item %d returned non-dict: %r", item_id, type(body).__name__)
            return None
        return body

    async def get_items(self, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        results = await asyncio.gather(
            *[self._safe_get(item_id) for item_id in ids],
            return_exceptions=False,
        )
        return [item for item in results if item is not None]

    async def _safe_get(self, item_id: int) -> dict[str, Any] | None:
        try:
            return await self.get_item(item_id)
        except (httpx.HTTPError, ValueError):
            log.exception("HN fetch failed for item_id=%d", item_id)
            return None
