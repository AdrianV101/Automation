from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from audio_ingest.hacker_news_adapter import PollSummary, run_poll_cycle
from audio_ingest.hn_state import HackerNewsStateDB

FIXTURES = Path(__file__).parent / "fixtures" / "hn"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


class FakeClient:
    """Drop-in for HackerNewsClient with deterministic responses."""
    def __init__(self, top_ids, items_by_id):
        self._top_ids = top_ids
        self._items = items_by_id
        self.top_calls = 0
        self.item_calls: list[int] = []

    async def top_story_ids(self, limit):
        self.top_calls += 1
        return self._top_ids[:limit]

    async def get_items(self, ids):
        self.item_calls.extend(ids)
        return [self._items[i] for i in ids if i in self._items]


@pytest.fixture
async def state_db(tmp_path):
    db = HackerNewsStateDB(tmp_path / "state.db")
    await db.init_db()
    return db


@pytest.fixture
def vault(tmp_path):
    p = tmp_path / "vault"
    p.mkdir()
    return p


async def test_writes_filtered_items_and_returns_summary(state_db, vault):
    s, a, sh = _load("story_url.json"), _load("ask_hn.json"), _load("show_hn.json")
    client = FakeClient(
        top_ids=[s["id"], a["id"], sh["id"]],
        items_by_id={s["id"]: s, a["id"]: a, sh["id"]: sh},
    )
    summary = await run_poll_cycle(
        client, state_db, vault,
        min_points=100, max_items=25, topstories_pool=50,
    )
    assert isinstance(summary, PollSummary)
    assert summary.ingested == 3
    assert summary.skipped_dedupe == 0
    assert summary.fetch_failures == 0
    # Top by score: show_hn (540) > story_url (350) > ask_hn (210)
    assert summary.top_title == sh["title"]
    assert summary.top_points == 540


async def test_writes_three_files_under_dated_folder(state_db, vault):
    s, a, sh = _load("story_url.json"), _load("ask_hn.json"), _load("show_hn.json")
    client = FakeClient(
        top_ids=[s["id"], a["id"], sh["id"]],
        items_by_id={s["id"]: s, a["id"]: a, sh["id"]: sh},
    )
    await run_poll_cycle(
        client, state_db, vault,
        min_points=100, max_items=25, topstories_pool=50,
    )
    # Fixtures all have time=1746086400 / 1746086500 / 1746086600 (2025-05-01 UTC).
    folder = vault / "00-Inbox" / "news" / "2025-05-01"
    assert folder.is_dir()
    assert len(list(folder.glob("*.md"))) == 3


async def test_second_poll_dedupes_zero_writes(state_db, vault):
    s = _load("story_url.json")
    client = FakeClient(top_ids=[s["id"]], items_by_id={s["id"]: s})
    s1 = await run_poll_cycle(
        client, state_db, vault,
        min_points=100, max_items=25, topstories_pool=50,
    )
    s2 = await run_poll_cycle(
        client, state_db, vault,
        min_points=100, max_items=25, topstories_pool=50,
    )
    assert s1.ingested == 1
    assert s2.ingested == 0
    assert s2.skipped_dedupe == 1


async def test_filters_below_threshold(state_db, vault):
    low = {**_load("story_url.json"), "score": 50}
    client = FakeClient(top_ids=[low["id"]], items_by_id={low["id"]: low})
    summary = await run_poll_cycle(
        client, state_db, vault,
        min_points=100, max_items=25, topstories_pool=50,
    )
    assert summary.ingested == 0


async def test_sets_checkpoint_to_largest_seen_id(state_db, vault):
    s, a, sh = _load("story_url.json"), _load("ask_hn.json"), _load("show_hn.json")
    client = FakeClient(
        top_ids=[s["id"], a["id"], sh["id"]],
        items_by_id={s["id"]: s, a["id"]: a, sh["id"]: sh},
    )
    await run_poll_cycle(
        client, state_db, vault,
        min_points=100, max_items=25, topstories_pool=50,
    )
    expected = max(s["id"], a["id"], sh["id"])
    assert await state_db.get_checkpoint() == expected


async def test_per_item_write_failure_does_not_record_processed(state_db, vault, monkeypatch):
    """If write_news_item raises for one item, others still ingest and the
    failed item is NOT marked processed (so it retries tomorrow)."""
    s, a = _load("story_url.json"), _load("ask_hn.json")
    client = FakeClient(
        top_ids=[s["id"], a["id"]],
        items_by_id={s["id"]: s, a["id"]: a},
    )
    real_write = __import__(
        "audio_ingest.hacker_news_adapter", fromlist=["write_news_item"],
    ).write_news_item

    def flaky(item, vault_root):
        if "Ask" in item.subject:
            raise OSError("disk full")
        return real_write(item, vault_root)

    monkeypatch.setattr(
        "audio_ingest.hacker_news_adapter.write_news_item", flaky,
    )
    summary = await run_poll_cycle(
        client, state_db, vault,
        min_points=100, max_items=25, topstories_pool=50,
    )
    assert summary.ingested == 1
    assert summary.write_failures == 1
    assert await state_db.is_processed(f"hn-{s['id']}") is True
    assert await state_db.is_processed(f"hn-{a['id']}") is False


async def test_top_story_ids_failure_aborts_poll_with_summary(state_db, vault):
    """Whole poll fails up front -> run_poll_cycle raises (caller handles)."""
    class BoomClient:
        async def top_story_ids(self, limit):
            raise httpx.HTTPError("network down")
        async def get_items(self, ids):
            return []
    import httpx
    with pytest.raises(httpx.HTTPError):
        await run_poll_cycle(
            BoomClient(), state_db, vault,
            min_points=100, max_items=25, topstories_pool=50,
        )
