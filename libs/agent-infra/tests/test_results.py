import pytest

from agent_infra.results import AgentLoopResult, SessionResponse


class TestAgentLoopResult:
    def test_defaults(self):
        r = AgentLoopResult()
        assert r.text_parts == []
        assert r.files_written == []
        assert r.turns_used == 0
        assert r.error is None

    def test_mutable(self):
        r = AgentLoopResult()
        r.text_parts.append("hello")
        r.files_written.append("note.md")
        r.turns_used = 3
        r.error = "something failed"
        assert r.text_parts == ["hello"]
        assert r.files_written == ["note.md"]
        assert r.turns_used == 3
        assert r.error == "something failed"

    def test_independent_defaults(self):
        """Each instance gets its own list (no shared mutable default)."""
        r1 = AgentLoopResult()
        r2 = AgentLoopResult()
        r1.text_parts.append("only in r1")
        assert r2.text_parts == []


class TestSessionResponse:
    def test_defaults(self):
        r = SessionResponse(text="hello")
        assert r.text == "hello"
        assert r.files_written == []
        assert r.session_id is None
        assert r.error is None

    def test_all_fields(self):
        r = SessionResponse(
            text="response",
            files_written=["a.md", "b.md"],
            session_id="sess-456",
            error="partial",
        )
        assert r.text == "response"
        assert r.files_written == ["a.md", "b.md"]
        assert r.session_id == "sess-456"
        assert r.error == "partial"

    def test_frozen_immutable(self):
        r = SessionResponse(text="hello")
        with pytest.raises(AttributeError):
            r.text = "changed"
