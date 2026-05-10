"""Tests for news_personal_digest.runner — orchestration with mocked deps."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from audio_ingest.news_personal_digest.runner import (
    AgentRunInput,
    AgentRunOutput,
    DigestRunnerConfig,
    run_for_date,
)
from audio_ingest.news_personal_digest.state import (
    DigestItemInput,
    NewsDigestStateDB,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state.db"


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "01-Projects" / "News" / "daily").mkdir(parents=True)
    return root


@pytest.fixture
def cfg(vault_root: Path) -> DigestRunnerConfig:
    return DigestRunnerConfig(
        vault_root=vault_root,
        model="claude-opus-4-7",
        feedback_window_days=7,
    )


@pytest.mark.asyncio
async def test_skipped_no_master_when_master_doc_absent(
    db_path: Path, cfg: DigestRunnerConfig,
) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    run_agent = AsyncMock()
    notify = AsyncMock()
    send_messages = AsyncMock()

    await run_for_date(
        date(2026, 5, 9),
        db=db, config=cfg,
        run_agent=run_agent, notify=notify, send_messages=send_messages,
    )

    row = await db.get_run(date(2026, 5, 9))
    assert row["status"] == "skipped_no_master"
    run_agent.assert_not_awaited()
    send_messages.assert_not_awaited()
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexpected_exception_marks_failed_and_alerts(
    db_path: Path, cfg: DigestRunnerConfig,
) -> None:
    """Belt-and-braces: any uncaught exception in the inner path leaves the
    row as 'failed' (never stuck 'running') and emits a Telegram alert."""
    db = NewsDigestStateDB(db_path)
    await db.init_db()

    async def boom(_inp: AgentRunInput) -> AgentRunOutput:
        raise RuntimeError("kaboom")

    notify = AsyncMock()
    send_messages = AsyncMock()
    master = (cfg.vault_root / "01-Projects" / "News" / "daily"
              / "2026-05-09-master.md")
    master.write_text("# News Daily Master\n")

    await run_for_date(
        date(2026, 5, 9),
        db=db, config=cfg,
        run_agent=boom, notify=notify, send_messages=send_messages,
    )

    row = await db.get_run(date(2026, 5, 9))
    assert row["status"] == "failed"
    assert "kaboom" in (row["error"] or "")
    notify.assert_awaited()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

from audio_ingest.news_personal_digest.render import (  # noqa: E402
    DigestCategory,
    DigestItem,
)


def _di(*, id: int = 0, title: str = "T", position: int = 1,
        url: str | None = "https://x", source_path: str = "p",
        briefing: str | None = None) -> DigestItem:
    return DigestItem(
        id=id, title=title, url=url,
        briefing=briefing or ("b" * 100),
        why_you_care="w",
        source_path=source_path, position=position,
    )


@pytest.mark.asyncio
async def test_happy_path_persists_items_sends_messages_and_completes(
    db_path: Path, cfg: DigestRunnerConfig,
) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    master = (cfg.vault_root / "01-Projects" / "News" / "daily"
              / "2026-05-09-master.md")
    master.write_text("# Master 2026-05-09\n")

    captured_prompt: dict[str, str] = {}

    async def fake_run_agent(inp: AgentRunInput) -> AgentRunOutput:
        captured_prompt["prompt"] = inp.prompt
        return AgentRunOutput(
            success=True,
            categories=[
                DigestCategory(
                    "AI", "🤖",
                    items=[_di(
                        id=0, title="Claude 4.7",
                        source_path="00-Inbox/news/2026-05-09/anthropic.md",
                        position=1,
                    )],
                ),
                DigestCategory(
                    "Finance", "💸",
                    items=[_di(
                        id=0, title="ECB", url=None,
                        source_path="00-Inbox/news/2026-05-09/ecb.md",
                        position=1,
                    )],
                ),
            ],
            rating_signal_summary="No prior ratings — neutral run.",
        )

    sent: list[tuple[str, dict]] = []

    async def fake_send_messages(messages: list[tuple[str, dict]]) -> list[int]:
        sent.extend(messages)
        return [10001 + i for i in range(len(messages))]

    notify = AsyncMock()
    await run_for_date(
        date(2026, 5, 9),
        db=db, config=cfg,
        run_agent=fake_run_agent,
        notify=notify, send_messages=fake_send_messages,
    )

    row = await db.get_run(date(2026, 5, 9))
    assert row["status"] == "completed"
    assert row["item_count"] == 2
    assert "neutral" in (row["rating_signal_summary"] or "")
    assert len(sent) == 2
    item1 = await db.get_digest_item(1)
    item2 = await db.get_digest_item(2)
    assert item1["title"] == "Claude 4.7"
    assert item1["telegram_message_id"] == 10001
    assert item2["title"] == "ECB"
    assert item2["telegram_message_id"] == 10002
    assert "## Recent feedback" in captured_prompt["prompt"]
    assert "(no recent ratings)" in captured_prompt["prompt"]


@pytest.mark.asyncio
async def test_happy_path_includes_recent_ratings_in_prompt(
    db_path: Path, cfg: DigestRunnerConfig,
) -> None:
    """Ratings persisted on a prior digest day appear in today's prompt."""
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    await db.insert_run(digest_date=date(2026, 5, 8))
    id_a = await db.insert_item(
        digest_date=date(2026, 5, 8),
        item=DigestItemInput("p1", "AI", "Claude 4.7", "u", 1),
    )
    id_b = await db.insert_item(
        digest_date=date(2026, 5, 8),
        item=DigestItemInput("p2", "Finance", "Generic Series B", None, 1),
    )
    await db.upsert_rating(item_id=id_a, rating="star")
    await db.upsert_rating(item_id=id_b, rating="thumbs_down")

    master = (cfg.vault_root / "01-Projects" / "News" / "daily"
              / "2026-05-09-master.md")
    master.write_text("# Master 2026-05-09\n")

    captured: dict[str, str] = {}

    async def fake_run_agent(inp: AgentRunInput) -> AgentRunOutput:
        captured["prompt"] = inp.prompt
        return AgentRunOutput(
            success=True, categories=[],
            rating_signal_summary="ok",
        )

    async def fake_send_messages(messages: list[tuple[str, dict]]) -> list[int]:
        return []

    await run_for_date(
        date(2026, 5, 9),
        db=db, config=cfg,
        run_agent=fake_run_agent,
        notify=AsyncMock(), send_messages=fake_send_messages,
    )
    assert "Claude 4.7" in captured["prompt"]
    assert "Generic Series B" in captured["prompt"]
    assert captured["prompt"].index("⭐") < captured["prompt"].index("👎")


@pytest.mark.asyncio
async def test_agent_raises_marks_failed_and_alerts(
    db_path: Path, cfg: DigestRunnerConfig,
) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    master = (cfg.vault_root / "01-Projects" / "News" / "daily"
              / "2026-05-09-master.md")
    master.write_text("# Master\n")

    async def boom(_inp: AgentRunInput) -> AgentRunOutput:
        raise TimeoutError("model server timeout")

    notify = AsyncMock()
    send_messages = AsyncMock()
    await run_for_date(
        date(2026, 5, 9),
        db=db, config=cfg,
        run_agent=boom, notify=notify, send_messages=send_messages,
    )
    row = await db.get_run(date(2026, 5, 9))
    assert row["status"] == "failed"
    assert "timeout" in (row["error"] or "").lower()
    notify.assert_awaited()
    send_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_returns_failure_marks_failed_verification(
    db_path: Path, cfg: DigestRunnerConfig,
) -> None:
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    master = (cfg.vault_root / "01-Projects" / "News" / "daily"
              / "2026-05-09-master.md")
    master.write_text("# Master\n")

    async def fake(_inp: AgentRunInput) -> AgentRunOutput:
        return AgentRunOutput(
            success=False, categories=[],
            error="briefing too short on item #2",
        )

    notify = AsyncMock()
    send_messages = AsyncMock()
    await run_for_date(
        date(2026, 5, 9),
        db=db, config=cfg,
        run_agent=fake, notify=notify, send_messages=send_messages,
    )
    row = await db.get_run(date(2026, 5, 9))
    assert row["status"] == "failed_verification"
    assert "briefing too short" in (row["error"] or "")
    notify.assert_awaited()
    send_messages.assert_not_awaited()


# ---------------------------------------------------------------------------
# parse_agent_summary
# ---------------------------------------------------------------------------

from audio_ingest.news_personal_digest.runner import (  # noqa: E402
    parse_agent_summary,
)


def test_parse_agent_summary_happy_path() -> None:
    text = """
Some preamble from the agent.

```json
{
  "success": true,
  "rating_signal_summary": "Boosted AI items based on 3 ⭐.",
  "categories": [
    {
      "name": "AI",
      "emoji": "🤖",
      "items": [
        {
          "source_path": "00-Inbox/news/2026-05-09/anthropic.md",
          "title": "Anthropic 4.7",
          "url": "https://anthropic.com/...",
          "briefing": "Larger context window (1M default). Faster cache hits. Definitely more than the 80-character minimum.",
          "why_you_care": "Daily-driver model."
        }
      ]
    }
  ]
}
```
""".strip()
    out = parse_agent_summary([text])
    assert out.success is True
    assert out.error is None
    assert len(out.categories) == 1
    assert out.categories[0].name == "AI"
    assert out.categories[0].items[0].title == "Anthropic 4.7"
    assert out.categories[0].items[0].position == 1
    assert "Boosted" in out.rating_signal_summary


def test_parse_agent_summary_invalid_json_returns_failure() -> None:
    out = parse_agent_summary(["```json\n{not valid}\n```"])
    assert out.success is False
    assert "invalid" in (out.error or "").lower() or "json" in (out.error or "").lower()


def test_parse_agent_summary_no_json_block_returns_failure() -> None:
    out = parse_agent_summary(["just plain prose, no json block"])
    assert out.success is False
    assert "json" in (out.error or "").lower()


def test_parse_agent_summary_verifies_min_briefing_length() -> None:
    text = """
```json
{"success": true, "categories": [{"name":"AI","emoji":"🤖","items":[
{"source_path":"p","title":"T","url":"u","briefing":"short","why_you_care":"w"}
]}], "rating_signal_summary":""}
```
""".strip()
    out = parse_agent_summary([text])
    assert out.success is False
    assert "briefing" in (out.error or "").lower()


def test_parse_agent_summary_assigns_positions_per_category() -> None:
    long_b = "x" * 100
    text = (
        "```json\n"
        '{"success": true, "rating_signal_summary": "", "categories": [\n'
        '  {"name": "AI", "emoji": "🤖", "items": [\n'
        '    {"source_path":"p1","title":"A","url":null,'
        f'"briefing":"{long_b}","why_you_care":"w"' "},\n"
        '    {"source_path":"p2","title":"B","url":null,'
        f'"briefing":"{long_b}","why_you_care":"w"' "}\n"
        '  ]},\n'
        '  {"name": "Finance", "emoji": "💸", "items": [\n'
        '    {"source_path":"p3","title":"C","url":null,'
        f'"briefing":"{long_b}","why_you_care":"w"' "}\n"
        '  ]}\n'
        ']}\n'
        "```"
    )
    out = parse_agent_summary([text])
    assert out.success is True, out.error
    ai = out.categories[0]
    fin = out.categories[1]
    assert [it.position for it in ai.items] == [1, 2]
    assert [it.position for it in fin.items] == [1]
