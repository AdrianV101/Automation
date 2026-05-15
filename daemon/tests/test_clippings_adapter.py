import asyncio
import pytest
from automation_daemon.clippings_adapter import wait_until_stable


async def test_wait_until_stable_returns_true_for_settled_file(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("done", encoding="utf-8")
    assert await wait_until_stable(p, settle_s=0.05, poll_s=0.01, timeout_s=1.0) is True


async def test_wait_until_stable_detects_growing_file(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("x", encoding="utf-8")

    async def grow():
        for i in range(5):
            await asyncio.sleep(0.02)
            p.write_text("x" * (i + 2), encoding="utf-8")

    task = asyncio.create_task(grow())
    # While growing, it should not settle within a tight timeout.
    result = await wait_until_stable(p, settle_s=0.05, poll_s=0.01, timeout_s=0.08)
    await task
    assert result is False


async def test_wait_until_stable_false_if_missing(tmp_path):
    assert await wait_until_stable(tmp_path / "nope.md", settle_s=0.05, poll_s=0.01, timeout_s=0.2) is False
