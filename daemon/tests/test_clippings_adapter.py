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


# ---------------------------------------------------------------------------
# process_clipping tests
# ---------------------------------------------------------------------------

from unittest.mock import AsyncMock, patch
from automation_daemon.clippings_adapter import process_clipping
from automation_daemon.clippings_router import RouteOutcome
from automation_daemon.clippings_state import ClippingsStateDB


@pytest.fixture
async def state(tmp_path):
    d = ClippingsStateDB(tmp_path / "s.db")
    await d.init_db()
    return d


def _clip(tmp_path, name="A.md", source="https://x.com/a"):
    p = tmp_path / "Clippings"
    p.mkdir(exist_ok=True)
    f = p / name
    f.write_text(f'---\ntitle: "A"\nsource: "{source}"\n---\nbody\n', encoding="utf-8")
    return f


async def test_already_routed_is_skipped_without_routing(tmp_path, state):
    f = _clip(tmp_path)
    from automation_daemon.clippings_state import parse_clipping, clipping_key
    fm, body = parse_clipping(f)
    await state.insert_pending(clipping_key(fm, body), f.name)
    await state.mark_routed(clipping_key(fm, body), "somewhere.md")
    route_mock = AsyncMock()
    notify = AsyncMock()
    with patch("automation_daemon.clippings_adapter.route_clipping", route_mock):
        await process_clipping(f, vault_root=tmp_path, state=state,
                               telegram_notifier=notify, list_routing_targets=lambda: [])
    route_mock.assert_not_awaited()


async def test_routed_outcome_records_routed_and_notifies(tmp_path, state):
    f = _clip(tmp_path)
    outcome = RouteOutcome(kind="routed", routed_path="01-Projects/X/a.md", links_added=2)
    notify = AsyncMock()
    with patch("automation_daemon.clippings_adapter.route_clipping",
               AsyncMock(return_value=outcome)):
        await process_clipping(f, vault_root=tmp_path, state=state,
                               telegram_notifier=notify, list_routing_targets=lambda: [])
    from automation_daemon.clippings_state import parse_clipping, clipping_key
    fm, body = parse_clipping(f)
    assert (await state.get(clipping_key(fm, body)))["status"] == "routed"
    notify.assert_awaited()


async def test_needs_clarification_sets_pending_and_sends_keyboard(tmp_path, state):
    f = _clip(tmp_path)
    outcome = RouteOutcome(kind="needs_clarification", question="Which?",
                           candidates=["Next Steps", "Skip"])
    send_kb = AsyncMock(return_value=4321)  # returns telegram message_id
    with patch("automation_daemon.clippings_adapter.route_clipping",
               AsyncMock(return_value=outcome)):
        await process_clipping(f, vault_root=tmp_path, state=state,
                               telegram_notifier=AsyncMock(),
                               send_clarification=send_kb,
                               list_routing_targets=lambda: [])
    from automation_daemon.clippings_state import parse_clipping, clipping_key
    fm, body = parse_clipping(f)
    row = await state.get(clipping_key(fm, body))
    assert row["status"] == "pending_clarification"
    assert row["telegram_message_id"] == 4321
    send_kb.assert_awaited()


async def test_failed_outcome_marks_failed_and_leaves_file(tmp_path, state):
    f = _clip(tmp_path)
    outcome = RouteOutcome(kind="failed", error="bad sentinel")
    notify = AsyncMock()
    with patch("automation_daemon.clippings_adapter.route_clipping",
               AsyncMock(return_value=outcome)):
        await process_clipping(f, vault_root=tmp_path, state=state,
                               telegram_notifier=notify, list_routing_targets=lambda: [])
    assert f.exists()  # never moved on failure
    from automation_daemon.clippings_state import parse_clipping, clipping_key
    fm, body = parse_clipping(f)
    assert (await state.get(clipping_key(fm, body)))["status"] == "failed"
    notify.assert_awaited()


from automation_daemon.clippings_adapter import reconcile_clippings


async def test_reconcile_enqueues_unseen_and_failed_skips_terminal(tmp_path, state):
    cdir = tmp_path / "Clippings"
    cdir.mkdir()
    for n, src in [("new.md", "https://x.com/new"),
                   ("routed.md", "https://x.com/routed"),
                   ("failed.md", "https://x.com/failed")]:
        (cdir / n).write_text(f'---\ntitle: t\nsource: "{src}"\n---\nb\n', encoding="utf-8")
    from automation_daemon.clippings_state import parse_clipping, clipping_key
    rk = clipping_key(*parse_clipping(cdir / "routed.md"))
    await state.insert_pending(rk, "routed.md"); await state.mark_routed(rk, "x.md")
    fk = clipping_key(*parse_clipping(cdir / "failed.md"))
    await state.insert_pending(fk, "failed.md"); await state.mark_failed(fk)

    seen: list[str] = []
    async def fake_process(path, **kw): seen.append(path.name)

    with patch("automation_daemon.clippings_adapter.process_clipping", fake_process):
        await reconcile_clippings(
            clippings_dir=cdir, vault_root=tmp_path, state=state,
            telegram_notifier=AsyncMock(), list_routing_targets=lambda: [],
            send_clarification=AsyncMock(), max_failed_retries=3,
        )
    assert "new.md" in seen        # unseen -> enqueued
    assert "failed.md" in seen     # failed under retry cap -> re-enqueued
    assert "routed.md" not in seen # terminal -> skipped


async def test_reconcile_stops_retrying_after_cap(tmp_path, state):
    cdir = tmp_path / "Clippings"; cdir.mkdir()
    (cdir / "f.md").write_text('---\ntitle: t\nsource: "https://x.com/f"\n---\nb\n', encoding="utf-8")
    from automation_daemon.clippings_state import parse_clipping, clipping_key
    k = clipping_key(*parse_clipping(cdir / "f.md"))
    await state.insert_pending(k, "f.md")
    for _ in range(3):
        await state.mark_failed(k)  # retry_count == 3
    seen = []
    async def fake_process(path, **kw): seen.append(path.name)
    with patch("automation_daemon.clippings_adapter.process_clipping", fake_process):
        await reconcile_clippings(
            clippings_dir=cdir, vault_root=tmp_path, state=state,
            telegram_notifier=AsyncMock(), list_routing_targets=lambda: [],
            send_clarification=AsyncMock(), max_failed_retries=3,
        )
    assert seen == []  # at cap -> not retried
