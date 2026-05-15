"""Tests for news_personal_digest.runner — orchestration with mocked deps."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from automation_daemon.news_personal_digest.runner import (
    AgentRunInput,
    AgentRunOutput,
    DigestRunnerConfig,
    run_for_date,
)
from automation_daemon.news_personal_digest.state import (
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

from automation_daemon.news_personal_digest.render import (  # noqa: E402
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
async def test_agent_retries_once_on_exception_then_succeeds(
    db_path: Path, vault_root: Path,
) -> None:
    """First agent attempt raises; second succeeds. Run should complete
    cleanly using the retry-backoff config, with no duplicate item rows."""
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    master = (vault_root / "01-Projects" / "News" / "daily"
              / "2026-05-09-master.md")
    master.write_text("# Master\n")

    # Zero backoff so the test doesn't sleep.
    cfg_no_wait = DigestRunnerConfig(
        vault_root=vault_root, model="claude-opus-4-7",
        feedback_window_days=7,
        agent_retry_backoff_seconds=(0.0, 0.0),
    )

    calls = {"n": 0}

    async def flaky_agent(_inp: AgentRunInput) -> AgentRunOutput:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("transient network glitch")
        return AgentRunOutput(
            success=True,
            categories=[DigestCategory("AI", "🤖", items=[_di(
                title="x", source_path="00-Inbox/news/2026-05-09/x.md",
            )])],
            rating_signal_summary="recovered",
        )

    sent: list[tuple[str, dict]] = []

    async def send_messages(messages):
        sent.extend(messages)
        return [10001 + i for i in range(len(messages))]

    await run_for_date(
        date(2026, 5, 9),
        db=db, config=cfg_no_wait,
        run_agent=flaky_agent, notify=AsyncMock(),
        send_messages=send_messages,
    )

    assert calls["n"] == 2
    row = await db.get_run(date(2026, 5, 9))
    assert row["status"] == "completed"
    assert row["item_count"] == 1
    # Exactly one item row — retry didn't duplicate.
    assert (await db.get_digest_item(1))["title"] == "x"
    assert (await db.get_digest_item(2)) is None


@pytest.mark.asyncio
async def test_agent_exhausts_retries_marks_failed(
    db_path: Path, vault_root: Path,
) -> None:
    """Every retry attempt raises — final state is `failed` with the
    last exception captured in `error`."""
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    master = (vault_root / "01-Projects" / "News" / "daily"
              / "2026-05-09-master.md")
    master.write_text("# Master\n")
    cfg_no_wait = DigestRunnerConfig(
        vault_root=vault_root, model="claude-opus-4-7",
        feedback_window_days=7,
        agent_retry_backoff_seconds=(0.0, 0.0),
    )
    calls = {"n": 0}

    async def always_fails(_inp: AgentRunInput) -> AgentRunOutput:
        calls["n"] += 1
        raise TimeoutError(f"attempt {calls['n']} timeout")

    notify = AsyncMock()
    await run_for_date(
        date(2026, 5, 9),
        db=db, config=cfg_no_wait,
        run_agent=always_fails, notify=notify,
        send_messages=AsyncMock(),
    )

    assert calls["n"] == 2
    row = await db.get_run(date(2026, 5, 9))
    assert row["status"] == "failed"
    assert "timeout" in (row["error"] or "").lower()
    # Notify message mentions both attempts.
    notify.assert_awaited()
    msg = notify.call_args.args[0]
    assert "2 attempt" in msg


@pytest.mark.asyncio
async def test_partial_send_failure_keeps_alignment_and_notifies(
    db_path: Path, cfg: DigestRunnerConfig,
) -> None:
    """When send_messages returns [id, None, id] for three messages,
    only the successful ones get telegram_message_id attached, items
    in the failed message stay NULL, and the operator is notified."""
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    master = (cfg.vault_root / "01-Projects" / "News" / "daily"
              / "2026-05-09-master.md")
    master.write_text("# Master\n")

    async def fake_agent(_inp: AgentRunInput) -> AgentRunOutput:
        return AgentRunOutput(
            success=True,
            categories=[
                DigestCategory("AI", "🤖", items=[
                    _di(id=0, title="ai-1", position=1,
                        source_path="p1"),
                ]),
                DigestCategory("Finance", "💸", items=[
                    _di(id=0, title="fin-1", position=1,
                        source_path="p2"),
                ]),
                DigestCategory("Tech", "🛠", items=[
                    _di(id=0, title="tech-1", position=1,
                        source_path="p3"),
                ]),
            ],
            rating_signal_summary="three categories",
        )

    async def partial_sender(messages):
        # Middle message fails.
        return [10001, None, 10003]

    notify = AsyncMock()
    await run_for_date(
        date(2026, 5, 9),
        db=db, config=cfg,
        run_agent=fake_agent, notify=notify,
        send_messages=partial_sender,
    )

    row = await db.get_run(date(2026, 5, 9))
    assert row["status"] == "completed"
    assert "delivery: 2/3" in (row["rating_signal_summary"] or "")
    notify.assert_awaited()
    # Item 1 (AI) attached to msg 10001; item 2 (Finance, failed send) stays
    # NULL; item 3 (Tech) attached to msg 10003.
    assert (await db.get_digest_item(1))["telegram_message_id"] == 10001
    assert (await db.get_digest_item(2))["telegram_message_id"] is None
    assert (await db.get_digest_item(3))["telegram_message_id"] == 10003


@pytest.mark.asyncio
async def test_partial_send_with_split_category_preserves_intra_category_alignment(
    db_path: Path, cfg: DigestRunnerConfig,
) -> None:
    """A single oversize category splits into two messages. The first
    succeeds, the second fails. Items in the first message must be
    correctly attached; items in the second message must stay NULL —
    the runner's `flat_items[offset:offset+row_count]` slicing must
    not bleed across the split."""
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    master = (cfg.vault_root / "01-Projects" / "News" / "daily"
              / "2026-05-09-master.md")
    master.write_text("# Master\n")

    # Three items, each with a long briefing → render splits into >1 message.
    big = "x" * 1500

    async def fake_agent(_inp: AgentRunInput) -> AgentRunOutput:
        return AgentRunOutput(
            success=True,
            categories=[DigestCategory("AI", "🤖", items=[
                _di(id=0, title="a", briefing=big, position=1,
                    source_path="p1"),
                _di(id=0, title="b", briefing=big, position=2,
                    source_path="p2"),
                _di(id=0, title="c", briefing=big, position=3,
                    source_path="p3"),
            ])],
            rating_signal_summary="split test",
        )

    sent_count = {"n": 0}

    async def split_partial_sender(messages):
        # Confirm we got multiple messages from the splitter; fail the
        # last one only.
        sent_count["n"] = len(messages)
        return [10000 + i for i in range(len(messages) - 1)] + [None]

    await run_for_date(
        date(2026, 5, 9),
        db=db, config=cfg,
        run_agent=fake_agent, notify=AsyncMock(),
        send_messages=split_partial_sender,
    )

    assert sent_count["n"] >= 2, "render should have split the category"

    row = await db.get_run(date(2026, 5, 9))
    assert row["status"] == "completed"

    # Items 1, 2, 3 each persisted; the first N-1 messages succeeded so
    # items in those slices have telegram_message_id set; the items in
    # the final failed message stay NULL. We can't predict the split
    # boundary precisely without re-running render, but we can assert
    # at least one item has a real id AND at least one item has NULL.
    items = [await db.get_digest_item(i) for i in (1, 2, 3)]
    attached = [it["telegram_message_id"] for it in items]
    assert any(a is not None for a in attached), \
        "early-message items should be attached"
    assert any(a is None for a in attached), \
        "final-message items should remain unattached"
    # Verify no item is attached to a non-existent message id
    for it, a in zip(items, attached):
        if a is not None:
            assert a < 10000 + sent_count["n"], \
                f"item {it['title']} attached to invalid msg_id {a}"


@pytest.mark.asyncio
async def test_sender_returns_wrong_length_marks_failed(
    db_path: Path, cfg: DigestRunnerConfig,
) -> None:
    """A misbehaving sender that returns the wrong-length list MUST be
    refused rather than silently misroute."""
    db = NewsDigestStateDB(db_path)
    await db.init_db()
    master = (cfg.vault_root / "01-Projects" / "News" / "daily"
              / "2026-05-09-master.md")
    master.write_text("# Master\n")

    async def fake_agent(_inp: AgentRunInput) -> AgentRunOutput:
        return AgentRunOutput(
            success=True,
            categories=[
                DigestCategory("AI", "🤖", items=[
                    _di(id=0, title="ai-1", position=1, source_path="p1"),
                ]),
                DigestCategory("Finance", "💸", items=[
                    _di(id=0, title="fin-1", position=1, source_path="p2"),
                ]),
            ],
            rating_signal_summary="",
        )

    async def broken_sender(messages):
        # Two messages in, one id out.
        return [10001]

    notify = AsyncMock()
    await run_for_date(
        date(2026, 5, 9),
        db=db, config=cfg,
        run_agent=fake_agent, notify=notify,
        send_messages=broken_sender,
    )
    row = await db.get_run(date(2026, 5, 9))
    assert row["status"] == "failed"
    assert "length mismatch" in (row["error"] or "").lower()
    notify.assert_awaited()
    # No attachment may leak — both items must be left orphan even though
    # the items themselves were persisted before the send attempt.
    assert (await db.get_digest_item(1))["telegram_message_id"] is None
    assert (await db.get_digest_item(2))["telegram_message_id"] is None


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

from automation_daemon.news_personal_digest.runner import (  # noqa: E402
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
