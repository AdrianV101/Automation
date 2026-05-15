"""Tests for orchestrator-level dispatch glue introduced for the digest."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from automation_daemon.orchestrator import dispatch_callback_query


@pytest.mark.asyncio
async def test_dispatch_routes_nr_prefix_to_digest_handler() -> None:
    digest = AsyncMock()
    other = AsyncMock()
    await dispatch_callback_query(
        callback_query_id="c", message_id=10001, data="nr:42:up",
        digest_handler=digest, fallback_handler=other,
    )
    digest.assert_awaited_once()
    other.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_routes_unrecognised_to_fallback() -> None:
    digest = AsyncMock()
    other = AsyncMock()
    await dispatch_callback_query(
        callback_query_id="c", message_id=10001,
        data="speaker:label:5", digest_handler=digest,
        fallback_handler=other,
    )
    digest.assert_not_awaited()
    other.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_with_no_fallback_no_op_on_unrecognised() -> None:
    digest = AsyncMock()
    await dispatch_callback_query(
        callback_query_id="c", message_id=10001, data="weird",
        digest_handler=digest, fallback_handler=None,
    )
    digest.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_with_no_digest_handler_routes_all_to_fallback() -> None:
    """When digest is disabled, all callbacks (including 'nr:') go to fallback."""
    other = AsyncMock()
    await dispatch_callback_query(
        callback_query_id="c", message_id=10001, data="nr:42:up",
        digest_handler=None, fallback_handler=other,
    )
    other.assert_awaited_once()
