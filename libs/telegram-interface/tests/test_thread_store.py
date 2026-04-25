from __future__ import annotations

import pytest

from agent_infra import SessionStore
from telegram_interface import ThreadStore, ThreadRecord


class TestThreadStore:
    @pytest.fixture
    async def store(self, tmp_path):
        store = ThreadStore(tmp_path / "test.db")
        await store.init_db()
        return store

    # --- SessionStore protocol ---

    def test_implements_session_store(self, tmp_path):
        store = ThreadStore(tmp_path / "proto.db")
        assert isinstance(store, SessionStore)

    @pytest.mark.asyncio
    async def test_get_and_save_session_id(self, store):
        await store.save_session_id("42", "sess-abc")
        assert await store.get_session_id("42") == "sess-abc"

    @pytest.mark.asyncio
    async def test_get_session_id_missing(self, store):
        assert await store.get_session_id("999") is None

    @pytest.mark.asyncio
    async def test_get_session_id_non_integer_key(self, store):
        assert await store.get_session_id("not-a-number") is None

    @pytest.mark.asyncio
    async def test_save_session_id_non_integer_key(self, store):
        """save_session_id with non-integer key is a silent no-op."""
        await store.save_session_id("abc", "sess-1")  # should not raise
        # Nothing persisted
        assert await store.get_session_id("abc") is None

    @pytest.mark.asyncio
    async def test_get_session_id_with_prefixed_key(self, store):
        """SessionStore methods accept 'thread-42' prefixed keys."""
        await store.save_session_id("thread-42", "sess-prefixed")
        assert await store.get_session_id("thread-42") == "sess-prefixed"
        # Also accessible via plain integer key
        assert await store.get_session_id("42") == "sess-prefixed"

    @pytest.mark.asyncio
    async def test_save_session_id_prefixed_key_updates_existing(self, store):
        """save_session_id with prefixed key updates record created via save_thread."""
        await store.save_thread(thread_id=7, session_id="old", name="t", command="ask")
        await store.save_session_id("thread-7", "new-sess")
        record = await store.get_thread(7)
        assert record.session_id == "new-sess"

    @pytest.mark.asyncio
    async def test_get_session_id_after_save_thread(self, store):
        """SessionStore.get_session_id works for records created via save_thread."""
        await store.save_thread(
            thread_id=101, session_id="sess-xyz", name="Ask: funding", command="ask",
        )
        assert await store.get_session_id("101") == "sess-xyz"

    # --- Telegram-specific methods ---

    @pytest.mark.asyncio
    async def test_save_and_get_thread(self, store):
        await store.save_thread(
            thread_id=101, session_id="sess-abc", name="Ask: funding", command="ask",
        )
        record = await store.get_thread(101)
        assert record is not None
        assert record.thread_id == 101
        assert record.session_id == "sess-abc"
        assert record.name == "Ask: funding"
        assert record.command == "ask"
        assert record.created_at is not None

    @pytest.mark.asyncio
    async def test_get_thread_returns_none_for_missing(self, store):
        assert await store.get_thread(999) is None

    @pytest.mark.asyncio
    async def test_update_session_id(self, store):
        await store.save_thread(thread_id=1, session_id="old", name="t", command="note")
        await store.update_session_id(1, "new-sess")
        record = await store.get_thread(1)
        assert record.session_id == "new-sess"

    @pytest.mark.asyncio
    async def test_list_threads(self, store):
        await store.save_thread(thread_id=1, session_id="s1", name="t1", command="ask")
        await store.save_thread(thread_id=2, session_id="s2", name="t2", command="note")
        threads = await store.list_threads()
        assert len(threads) == 2

    @pytest.mark.asyncio
    async def test_save_thread_upsert_preserves_created_at(self, store):
        """Saving the same thread_id again updates session_id but preserves created_at."""
        await store.save_thread(thread_id=42, session_id="old-sess", name="Ask: test", command="ask")
        record1 = await store.get_thread(42)

        await store.save_thread(thread_id=42, session_id="new-sess", name="Ask: test", command="ask")
        record2 = await store.get_thread(42)

        assert record2.session_id == "new-sess"
        assert record2.created_at == record1.created_at

    @pytest.mark.asyncio
    async def test_frozen_thread_record(self, store):
        """ThreadRecord is frozen and cannot be mutated."""
        await store.save_thread(thread_id=1, session_id="s", name="t", command="ask")
        record = await store.get_thread(1)
        with pytest.raises(AttributeError):
            record.session_id = "changed"

    @pytest.mark.asyncio
    async def test_init_db_is_idempotent(self, tmp_path):
        store = ThreadStore(tmp_path / "test.db")
        await store.init_db()
        await store.init_db()  # should not raise
        await store.save_thread(thread_id=1, session_id="s", name="t", command="ask")
        assert await store.get_thread(1) is not None
