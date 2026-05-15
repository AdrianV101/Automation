from __future__ import annotations

from datetime import date

import pytest

from audio_ingest.orchestrator import _build_news_chain_fn


@pytest.mark.asyncio
async def test_chain_order_master_research_digest() -> None:
    calls: list[str] = []

    async def master(d): calls.append("master")
    async def research(d): calls.append("research")
    async def digest(d): calls.append("digest")
    async def master_completed(d): return True

    chain = _build_news_chain_fn(
        master_fn=master,
        research_fn=research,
        digest_fn=digest,
        master_completed=master_completed,
    )
    await chain(date(2026, 5, 15))
    assert calls == ["master", "research", "digest"]


@pytest.mark.asyncio
async def test_research_failure_does_not_block_digest() -> None:
    calls: list[str] = []

    async def master(d): calls.append("master")
    async def research(d):
        calls.append("research")
        raise RuntimeError("research boom")
    async def digest(d): calls.append("digest")
    async def master_completed(d): return True

    chain = _build_news_chain_fn(
        master_fn=master, research_fn=research,
        digest_fn=digest, master_completed=master_completed,
    )
    await chain(date(2026, 5, 15))  # must not raise
    assert calls == ["master", "research", "digest"]


@pytest.mark.asyncio
async def test_master_not_completed_skips_research_and_digest() -> None:
    calls: list[str] = []

    async def master(d): calls.append("master")
    async def research(d): calls.append("research")
    async def digest(d): calls.append("digest")
    async def master_completed(d): return False

    chain = _build_news_chain_fn(
        master_fn=master, research_fn=research,
        digest_fn=digest, master_completed=master_completed,
    )
    await chain(date(2026, 5, 15))
    assert calls == ["master"]


@pytest.mark.asyncio
async def test_research_and_digest_optional() -> None:
    calls: list[str] = []

    async def master(d): calls.append("master")
    async def master_completed(d): return True

    chain = _build_news_chain_fn(
        master_fn=master, research_fn=None,
        digest_fn=None, master_completed=master_completed,
    )
    await chain(date(2026, 5, 15))
    assert calls == ["master"]
