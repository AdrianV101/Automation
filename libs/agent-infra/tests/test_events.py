import pytest

from agent_infra.events import TraceEvent


class TestTraceEvent:
    def test_tool_start_event(self):
        e = TraceEvent(kind="tool_start", tool_name="vault_read", tool_input={"path": "foo.md"})
        assert e.kind == "tool_start"
        assert e.tool_name == "vault_read"
        assert e.tool_input == {"path": "foo.md"}
        assert e.content is None

    def test_tool_result_event(self):
        e = TraceEvent(kind="tool_result", content="file contents here")
        assert e.kind == "tool_result"
        assert e.content == "file contents here"
        assert e.tool_name is None

    def test_text_event(self):
        e = TraceEvent(kind="text", content="The project has several phases.")
        assert e.kind == "text"
        assert e.content == "The project has several phases."

    def test_complete_event(self):
        e = TraceEvent(
            kind="complete", turns_used=5, cost_usd=0.12,
            files_written=["00-Inbox/note.md"],
        )
        assert e.turns_used == 5
        assert e.cost_usd == 0.12
        assert e.files_written == ["00-Inbox/note.md"]

    def test_error_event(self):
        e = TraceEvent(kind="error", content="Agent SDK query failed")
        assert e.kind == "error"

    def test_defaults_are_none(self):
        e = TraceEvent(kind="text")
        assert e.tool_name is None
        assert e.tool_input is None
        assert e.content is None
        assert e.files_written is None
        assert e.turns_used is None
        assert e.cost_usd is None

    def test_frozen_immutable(self):
        e = TraceEvent(kind="text", content="hello")
        with pytest.raises(AttributeError):
            e.content = "changed"

    def test_all_kinds(self):
        for kind in ("tool_start", "tool_result", "text", "complete", "error"):
            e = TraceEvent(kind=kind)
            assert e.kind == kind
