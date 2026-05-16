from automation_daemon.clippings_state import normalize_url


def test_normalize_lowercases_scheme_and_host():
    assert normalize_url("HTTPS://Example.COM/Path") == "https://example.com/Path"


def test_normalize_strips_fragment_and_trailing_slash():
    assert normalize_url("https://x.com/a/?") == "https://x.com/a"
    assert normalize_url("https://x.com/a#frag") == "https://x.com/a"


def test_normalize_strips_tracking_params_keeps_meaningful_query():
    assert (
        normalize_url("https://x.com/p?utm_source=t&fbclid=9&id=42&gclid=z")
        == "https://x.com/p?id=42"
    )


def test_normalize_drops_default_ports():
    assert normalize_url("https://x.com:443/p") == "https://x.com/p"
    assert normalize_url("http://x.com:80/p") == "http://x.com/p"


def test_normalize_empty_returns_empty():
    assert normalize_url("") == ""
    assert normalize_url("   ") == ""


import textwrap
from automation_daemon.clippings_state import parse_clipping, clipping_key


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


def test_parse_clipping_splits_frontmatter_and_body(tmp_path):
    p = _write(tmp_path, "c.md", """\
        ---
        title: "Hello"
        source: "https://x.com/a"
        tags:
          - clippings
        ---
        Body line one.
        """)
    fm, body = parse_clipping(p)
    assert fm["title"] == "Hello"
    assert fm["source"] == "https://x.com/a"
    assert body.strip() == "Body line one."


def test_parse_clipping_no_frontmatter(tmp_path):
    p = _write(tmp_path, "c.md", "just body, no frontmatter\n")
    fm, body = parse_clipping(p)
    assert fm == {}
    assert body.strip() == "just body, no frontmatter"


def test_clipping_key_prefers_source_url(tmp_path):
    assert clipping_key({"source": "https://X.com/a/?utm_source=z"}, "body") == "url:https://x.com/a"


def test_clipping_key_falls_back_to_content_hash():
    k = clipping_key({"source": ""}, "abc")
    assert k.startswith("hash:")
    assert k == clipping_key({}, "abc")  # same body → same key
    assert k != clipping_key({}, "abd")


import pytest
from automation_daemon.clippings_state import ClippingsStateDB


@pytest.fixture
async def db(tmp_path):
    d = ClippingsStateDB(tmp_path / "state.db")
    await d.init_db()
    return d


async def test_insert_then_get_pending(db):
    await db.insert_pending("url:https://x.com/a", "A.md")
    row = await db.get("url:https://x.com/a")
    assert row is not None
    assert row["status"] == "pending"
    assert row["original_filename"] == "A.md"
    assert row["retry_count"] == 0


async def test_mark_routed_and_skipped(db):
    await db.insert_pending("k1", "A.md")
    await db.mark_routed("k1", "01-Projects/Next Steps/clippings/A.md")
    assert (await db.get("k1"))["status"] == "routed"
    await db.insert_pending("k2", "B.md")
    await db.mark_skipped("k2")
    assert (await db.get("k2"))["status"] == "skipped"


async def test_mark_failed_increments_retry_count(db):
    await db.insert_pending("k", "A.md")
    await db.mark_failed("k")
    await db.mark_failed("k")
    row = await db.get("k")
    assert row["status"] == "failed"
    assert row["retry_count"] == 2


async def test_pending_clarification_lookup_by_message_and_oldest(db):
    await db.insert_pending("k1", "A.md")
    await db.set_pending_clarification("k1", candidates=["Next Steps", "Skip"], telegram_message_id=555)
    by_msg = await db.find_pending_clarification_by_message(555)
    assert by_msg["url_key"] == "k1"
    assert by_msg["candidates"] == ["Next Steps", "Skip"]
    oldest = await db.oldest_pending_clarification()
    assert oldest["url_key"] == "k1"


async def test_get_missing_returns_none(db):
    assert await db.get("nope") is None
    assert await db.find_pending_clarification_by_message(999) is None
    assert await db.oldest_pending_clarification() is None


async def test_all_pending_clarifications_returns_ordered_rows(db):
    # Insert two rows with deliberate ordering
    await db.insert_pending("k1", "A.md")
    await db.set_pending_clarification("k1", candidates=["X", "Skip"], telegram_message_id=11)
    await db.insert_pending("k2", "B.md")
    await db.set_pending_clarification("k2", candidates=["Y", "Skip"], telegram_message_id=22)
    rows = await db.all_pending_clarifications()
    assert len(rows) == 2
    # Both rows present (order stable by processed_at ASC insertion order)
    keys = [r["url_key"] for r in rows]
    assert "k1" in keys and "k2" in keys
    # k1 was inserted first so it should come first
    assert keys[0] == "k1"


async def test_all_pending_clarifications_empty(db):
    assert await db.all_pending_clarifications() == []


async def test_all_pending_clarifications_excludes_non_pending(db):
    await db.insert_pending("k1", "A.md")
    await db.set_pending_clarification("k1", candidates=["X", "Skip"], telegram_message_id=11)
    await db.insert_pending("k2", "B.md")
    await db.mark_failed("k2")
    rows = await db.all_pending_clarifications()
    assert len(rows) == 1
    assert rows[0]["url_key"] == "k1"
