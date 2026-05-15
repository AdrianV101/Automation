"""Tests for news_daily_master.runner.run_for_date."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from automation_daemon.news_daily_master.runner import (
    AgentRunInput,
    AgentRunOutput,
    RunnerConfig,
    run_for_date,
)
from automation_daemon.news_daily_master.state import NewsDailyMasterStateDB


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state.db"


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    return tmp_path / "vault"


@pytest.fixture
def runner_cfg(tmp_path: Path, vault_root: Path) -> RunnerConfig:
    return RunnerConfig(
        vault_root=vault_root,
        model="claude-opus-4-7",
        telegram_topic_id=42,
        retry_backoff_seconds=(0, 0, 0),  # no real waits in tests
    )


@pytest.mark.asyncio
async def test_run_for_date_no_source_folder_marks_skipped_empty(
    db_path: Path, vault_root: Path, runner_cfg: RunnerConfig,
) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    # vault_root/00-Inbox/news/2026-04-29 does not exist.
    agent_runner = AsyncMock()
    notifier = AsyncMock()

    await run_for_date(
        date(2026, 4, 29),
        db=db, config=runner_cfg,
        run_agent=agent_runner,
        notify=notifier,
    )

    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "skipped_empty"
    agent_runner.assert_not_called()
    notifier.assert_not_called()


@pytest.mark.asyncio
async def test_run_for_date_empty_folder_marks_skipped_empty(
    db_path: Path, vault_root: Path, runner_cfg: RunnerConfig,
) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    folder = vault_root / "00-Inbox" / "news" / "2026-04-29"
    folder.mkdir(parents=True)
    # No .md files inside.
    agent_runner = AsyncMock()
    notifier = AsyncMock()

    await run_for_date(
        date(2026, 4, 29),
        db=db, config=runner_cfg,
        run_agent=agent_runner,
        notify=notifier,
    )

    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "skipped_empty"
    agent_runner.assert_not_called()


def _seed_source_item(vault_root: Path, target_date: date, slug: str) -> Path:
    folder = vault_root / "00-Inbox" / "news" / target_date.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    note = folder / f"{slug}.md"
    note.write_text(
        "---\ntype: news-item\nsubject: test\n---\n# Test\n\nbody\n",
    )
    return note


@pytest.mark.asyncio
async def test_run_for_date_happy_path(
    db_path: Path, vault_root: Path, runner_cfg: RunnerConfig,
) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    _seed_source_item(vault_root, date(2026, 4, 29), "item-a")
    _seed_source_item(vault_root, date(2026, 4, 29), "item-b")

    agent_output = AgentRunOutput(
        success=True, item_count=2,
        categories=["AI", "Tech"], new_categories=[],
        skipped_items=[],
        text="ok",
    )
    # Simulate the agent writing the master doc as part of its run.
    async def fake_agent(inp: AgentRunInput) -> AgentRunOutput:
        master_path = (
            inp.vault_root / "01-Projects" / "News" / "daily"
            / f"{inp.target_date.isoformat()}-master.md"
        )
        master_path.parent.mkdir(parents=True, exist_ok=True)
        master_path.write_text(
            f"# News Daily Master — {inp.target_date.isoformat()}\n\n"
            "## AI\n- ...\n\n## Tech\n- ...\n\n"
            "## Notes\n",
        )
        return agent_output

    notifier = AsyncMock()
    await run_for_date(
        date(2026, 4, 29),
        db=db, config=runner_cfg,
        run_agent=fake_agent,
        notify=notifier,
    )

    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "completed"
    assert row["item_count"] == 2
    assert row["master_path"].endswith("2026-04-29-master.md")
    assert notifier.await_count == 1
    msg = notifier.await_args.args[0]
    assert "2026-04-29" in msg
    assert "2 items" in msg


@pytest.mark.asyncio
async def test_run_for_date_agent_returns_failure(
    db_path: Path, vault_root: Path, runner_cfg: RunnerConfig,
) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    _seed_source_item(vault_root, date(2026, 4, 29), "item-a")

    async def failing_agent(inp: AgentRunInput) -> AgentRunOutput:
        return AgentRunOutput(
            success=False, item_count=0, error="model said no",
        )

    notifier = AsyncMock()
    await run_for_date(
        date(2026, 4, 29),
        db=db, config=runner_cfg,
        run_agent=failing_agent,
        notify=notifier,
    )

    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "failed"
    assert row["error"] == "model said no"
    notifier.assert_awaited()
    err_msg = notifier.await_args.args[0]
    assert "failed" in err_msg.lower()


import hashlib


def _seed_master_doc_with_notes(
    vault_root: Path, target_date: date, notes_body: str,
) -> Path:
    daily = vault_root / "01-Projects" / "News" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    p = daily / f"{target_date.isoformat()}-master.md"
    p.write_text(
        f"# News Daily Master — {target_date.isoformat()}\n\n"
        "## AI\n- existing item\n\n"
        f"## Notes\n{notes_body}\n",
    )
    return p


@pytest.mark.asyncio
async def test_run_for_date_preserves_notes_section(
    db_path: Path, vault_root: Path, runner_cfg: RunnerConfig,
) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    _seed_source_item(vault_root, date(2026, 4, 29), "item-a")
    notes_body = "Adrian's annotation about today's items.\n"
    master_path = _seed_master_doc_with_notes(
        vault_root, date(2026, 4, 29), notes_body,
    )

    async def good_agent(inp: AgentRunInput) -> AgentRunOutput:
        # Simulate agent rewriting body but leaving Notes alone.
        master_path.write_text(
            f"# News Daily Master — {inp.target_date.isoformat()}\n\n"
            "## AI\n- new item\n\n"
            f"## Notes\n{notes_body}\n",
        )
        return AgentRunOutput(success=True, item_count=1, categories=["AI"])

    notifier = AsyncMock()
    await run_for_date(
        date(2026, 4, 29),
        db=db, config=runner_cfg,
        run_agent=good_agent, notify=notifier,
    )

    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "completed"


@pytest.mark.asyncio
async def test_run_for_date_detects_notes_clobber(
    db_path: Path, vault_root: Path, runner_cfg: RunnerConfig,
) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    _seed_source_item(vault_root, date(2026, 4, 29), "item-a")
    master_path = _seed_master_doc_with_notes(
        vault_root, date(2026, 4, 29), "Original annotation.\n",
    )

    async def clobbering_agent(inp: AgentRunInput) -> AgentRunOutput:
        # Agent (incorrectly) overwrites the Notes section.
        master_path.write_text(
            f"# News Daily Master — {inp.target_date.isoformat()}\n\n"
            "## AI\n- new item\n\n"
            "## Notes\nClobbered by agent.\n",
        )
        return AgentRunOutput(success=True, item_count=1, categories=["AI"])

    notifier = AsyncMock()
    await run_for_date(
        date(2026, 4, 29),
        db=db, config=runner_cfg,
        run_agent=clobbering_agent, notify=notifier,
    )

    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "failed_notes_clobbered"
    notifier.assert_awaited()
    msg = notifier.await_args.args[0]
    assert "notes" in msg.lower()


@pytest.mark.asyncio
async def test_run_for_date_verification_failure_when_agent_skipped_items(
    db_path: Path, vault_root: Path, runner_cfg: RunnerConfig,
) -> None:
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    _seed_source_item(vault_root, date(2026, 4, 29), "item-a")
    _seed_source_item(vault_root, date(2026, 4, 29), "item-b")

    async def partial_agent(inp: AgentRunInput) -> AgentRunOutput:
        # Agent reports success but skipped one item — represents a
        # verification-failure mode (item-b was unparseable, etc.).
        master_path = (
            inp.vault_root / "01-Projects" / "News" / "daily"
            / f"{inp.target_date.isoformat()}-master.md"
        )
        master_path.parent.mkdir(parents=True, exist_ok=True)
        master_path.write_text(
            f"# News Daily Master — {inp.target_date.isoformat()}\n\n"
            "## AI\n- one item\n\n## Notes\n",
        )
        return AgentRunOutput(
            success=True, item_count=1, categories=["AI"],
            skipped_items=["item-b: unparseable body"],
        )

    notifier = AsyncMock()
    await run_for_date(
        date(2026, 4, 29),
        db=db, config=runner_cfg,
        run_agent=partial_agent, notify=notifier,
    )

    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "failed_verification"
    assert "item-b" in (row["error"] or "")
    notifier.assert_awaited()


# ---------------------------------------------------------------------------
# Task K: run_agent_via_agent_infra adapter tests
# ---------------------------------------------------------------------------

import json as _json_test
from unittest.mock import patch

from conftest import (
    make_assistant_message,
    MockAssistantMessage,
    MockTextBlock,
    MockToolUseBlock,
)


@pytest.mark.asyncio
async def test_run_agent_via_agent_infra_parses_structured_summary(
    tmp_path: Path,
) -> None:
    """Agent emits a JSON summary in its final text block; we parse it."""
    from automation_daemon.news_daily_master.runner import run_agent_via_agent_infra

    summary = {
        "success": True,
        "item_count": 3,
        "categories": ["AI", "Tech"],
        "new_categories": ["Climate"],
        "skipped_items": [],
    }
    msg = make_assistant_message(text_blocks=[
        "Working...",
        f"```json\n{_json_test.dumps(summary)}\n```",
    ])

    async def fake_query(prompt, options):
        yield msg

    with (
        patch("agent_infra.runner.query", side_effect=fake_query),
        patch("agent_infra.runner.AssistantMessage", MockAssistantMessage),
        patch("agent_infra.runner.TextBlock", MockTextBlock),
        patch("agent_infra.runner.ToolUseBlock", MockToolUseBlock),
    ):
        inp = AgentRunInput(
            target_date=date(2026, 4, 29),
            vault_root=tmp_path,
            model="claude-opus-4-7",
        )
        output = await run_agent_via_agent_infra(inp)

    assert output.success is True
    assert output.item_count == 3
    assert output.categories == ["AI", "Tech"]
    assert output.new_categories == ["Climate"]


@pytest.mark.asyncio
async def test_run_agent_via_agent_infra_handles_missing_summary(
    tmp_path: Path,
) -> None:
    from automation_daemon.news_daily_master.runner import run_agent_via_agent_infra
    msg = make_assistant_message(text_blocks=["I did stuff but no JSON."])

    async def fake_query(prompt, options):
        yield msg

    with (
        patch("agent_infra.runner.query", side_effect=fake_query),
        patch("agent_infra.runner.AssistantMessage", MockAssistantMessage),
        patch("agent_infra.runner.TextBlock", MockTextBlock),
        patch("agent_infra.runner.ToolUseBlock", MockToolUseBlock),
    ):
        inp = AgentRunInput(
            target_date=date(2026, 4, 29),
            vault_root=tmp_path, model="claude-opus-4-7",
        )
        output = await run_agent_via_agent_infra(inp)

    assert output.success is False
    assert output.error is not None
    assert "summary" in output.error.lower()


# ---------------------------------------------------------------------------
# AgentRunOutput pair-invariant
# ---------------------------------------------------------------------------


class TestAgentRunOutputInvariants:
    """Mirror of the AgentRoutingResult success/error pair invariant."""

    def test_success_true_with_error_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="success=True.*error=None"):
            AgentRunOutput(success=True, item_count=1, error="actually failed")

    def test_success_false_without_error_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="success=False.*non-None error"):
            AgentRunOutput(success=False, item_count=0)

    def test_success_true_without_error_is_valid(self) -> None:
        out = AgentRunOutput(success=True, item_count=1)
        assert out.error is None

    def test_success_false_with_error_is_valid(self) -> None:
        out = AgentRunOutput(success=False, item_count=0, error="something broke")
        assert out.error == "something broke"


# ---------------------------------------------------------------------------
# _parse_agent_summary edge cases (multiple JSON blocks, missing-error fallback)
# ---------------------------------------------------------------------------


def test_parse_agent_summary_picks_last_json_block() -> None:
    """When the agent emits multiple JSON blocks, only the LAST one wins."""
    from automation_daemon.news_daily_master.runner import _parse_agent_summary

    intermediate = '{"success": false, "item_count": 0, "error": "still working"}'
    final = '{"success": true, "item_count": 5, "categories": ["AI"]}'
    text = (
        f"Working...\n```json\n{intermediate}\n```\n"
        f"More work...\n```json\n{final}\n```\n"
    )
    out = _parse_agent_summary([text])
    assert out.success is True
    assert out.item_count == 5
    assert out.categories == ["AI"]


def test_parse_agent_summary_invalid_json_returns_failure() -> None:
    from automation_daemon.news_daily_master.runner import _parse_agent_summary

    # Closing brace is required for the fenced-block regex to match the block
    # at all; the JSON inside is then what fails to parse.
    out = _parse_agent_summary(["```json\n{bad: json, missing: quotes}\n```"])
    assert out.success is False
    assert out.error is not None
    assert "invalid" in out.error.lower()


def test_parse_agent_summary_failure_without_error_field_synthesises_one() -> None:
    """Agent reports success=false but forgets to include `error` — we synthesise.

    Without this fallback, the AgentRunOutput pair-invariant would refuse to
    construct, and the runner would crash with an unhelpful error.
    """
    from automation_daemon.news_daily_master.runner import _parse_agent_summary

    out = _parse_agent_summary(['```json\n{"success": false, "item_count": 0}\n```'])
    assert out.success is False
    assert out.error  # truthy — some fallback message present


# ---------------------------------------------------------------------------
# _hash_notes_section error handling — bad bytes / permission errors
# ---------------------------------------------------------------------------


def test_hash_notes_section_returns_none_on_unicode_error(tmp_path: Path) -> None:
    """A non-UTF8 byte sequence in the master doc must not crash the runner."""
    from automation_daemon.news_daily_master.runner import _hash_notes_section

    p = tmp_path / "master.md"
    p.write_bytes(b"# Header\n\n## Notes\n\xff\xfe non-utf8 bytes\n")
    # Should swallow the decode error and return None (= "no prior hash").
    assert _hash_notes_section(p) is None


# ---------------------------------------------------------------------------
# run_for_date defensive try/except — DB row never stuck in 'running'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_for_date_unexpected_exception_marks_failed(
    monkeypatch: pytest.MonkeyPatch,
    db_path: Path, vault_root: Path, runner_cfg: RunnerConfig,
) -> None:
    """If something between insert_run and the final update_run raises (e.g.
    `_hash_notes_section` blowing up on an unexpected internal error), the row
    must transition out of 'running' so backfill doesn't see a stale stuck row."""
    db = NewsDailyMasterStateDB(db_path)
    await db.init_db()
    _seed_source_item(vault_root, date(2026, 4, 29), "item-a")

    def boom(_path):
        raise RuntimeError("hashing crashed unexpectedly")

    monkeypatch.setattr(
        "automation_daemon.news_daily_master.runner._hash_notes_section", boom,
    )

    async def fake_agent(inp: AgentRunInput) -> AgentRunOutput:
        return AgentRunOutput(success=True, item_count=1, categories=["AI"])

    notifier = AsyncMock()
    # Should NOT propagate the RuntimeError; the runner's outer guard catches it.
    await run_for_date(
        date(2026, 4, 29),
        db=db, config=runner_cfg,
        run_agent=fake_agent, notify=notifier,
    )

    row = await db.get_run(date(2026, 4, 29))
    assert row["status"] == "failed"  # not 'running'
    assert row["error"] is not None
    assert "hashing crashed unexpectedly" in row["error"]
    notifier.assert_awaited()
