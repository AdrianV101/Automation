from __future__ import annotations

import json
import httpx
import pytest

from automation_daemon.hn_client import HackerNewsClient, HN_API_BASE


def _mock_transport(routes):
    """routes: dict[str, dict | list | int(status)]."""
    async def handler(request):
        path = request.url.path
        if path not in routes:
            return httpx.Response(404)
        val = routes[path]
        if isinstance(val, int):
            return httpx.Response(val)
        return httpx.Response(200, content=json.dumps(val))
    return httpx.MockTransport(handler)


async def test_top_story_ids_slices_to_limit():
    transport = _mock_transport({"/v0/topstories.json": list(range(1, 100))})
    async with httpx.AsyncClient(transport=transport, base_url=HN_API_BASE) as http:
        client = HackerNewsClient(http)
        ids = await client.top_story_ids(limit=10)
    assert ids == list(range(1, 11))


async def test_top_story_ids_url_path():
    seen = []
    async def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, content="[1,2,3]")
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url=HN_API_BASE) as http:
        client = HackerNewsClient(http)
        await client.top_story_ids(limit=3)
    assert seen == ["/v0/topstories.json"]


async def test_get_item_returns_dict():
    transport = _mock_transport({
        "/v0/item/42.json": {"id": 42, "type": "story", "score": 200, "title": "x"},
    })
    async with httpx.AsyncClient(transport=transport, base_url=HN_API_BASE) as http:
        client = HackerNewsClient(http)
        item = await client.get_item(42)
    assert item is not None
    assert item["id"] == 42
    assert item["title"] == "x"


async def test_get_item_returns_none_for_404():
    transport = _mock_transport({"/v0/item/42.json": 404})
    async with httpx.AsyncClient(transport=transport, base_url=HN_API_BASE) as http:
        client = HackerNewsClient(http)
        item = await client.get_item(42)
    assert item is None


async def test_get_item_returns_none_for_null_body():
    """Firebase returns the JSON literal `null` for deleted items."""
    transport = _mock_transport({"/v0/item/42.json": None})
    async with httpx.AsyncClient(transport=transport, base_url=HN_API_BASE) as http:
        client = HackerNewsClient(http)
        item = await client.get_item(42)
    assert item is None


async def test_get_items_drops_failures_continues_others(caplog):
    routes = {
        "/v0/item/1.json": {"id": 1, "type": "story", "score": 100},
        "/v0/item/2.json": 500,
        "/v0/item/3.json": {"id": 3, "type": "story", "score": 200},
    }
    transport = _mock_transport(routes)
    async with httpx.AsyncClient(transport=transport, base_url=HN_API_BASE) as http:
        client = HackerNewsClient(http)
        items = await client.get_items([1, 2, 3])
    ids = sorted(it["id"] for it in items)
    assert ids == [1, 3]


async def test_get_items_empty_returns_empty():
    transport = _mock_transport({})
    async with httpx.AsyncClient(transport=transport, base_url=HN_API_BASE) as http:
        client = HackerNewsClient(http)
        items = await client.get_items([])
    assert items == []
