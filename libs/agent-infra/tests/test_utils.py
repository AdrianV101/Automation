from datetime import datetime

from agent_infra.utils import extract_file_path, parse_date


class TestParseDate:
    def test_iso_with_offset(self):
        dt = parse_date("2026-02-06T12:00:00+00:00")
        assert dt.year == 2026
        assert dt.month == 2
        assert dt.day == 6

    def test_iso_with_z(self):
        dt = parse_date("2026-01-15T08:30:00Z")
        assert dt.year == 2026
        assert dt.month == 1

    def test_invalid_string_falls_back_to_now(self):
        dt = parse_date("not-a-date")
        assert isinstance(dt, datetime)
        # Should be roughly now
        assert (datetime.now() - dt).total_seconds() < 5

    def test_none_falls_back_to_now(self):
        dt = parse_date(None)
        assert isinstance(dt, datetime)


class TestExtractFilePath:
    def test_vault_write(self):
        result = extract_file_path(
            "mcp__obsidian-pkm__vault_write",
            {"path": "00-Inbox/test.md", "template": "fleeting-note"},
        )
        assert result == "00-Inbox/test.md"

    def test_vault_append(self):
        result = extract_file_path(
            "mcp__obsidian-pkm__vault_append",
            {"path": "01-Projects/Automation/devlog.md", "content": "stuff"},
        )
        assert result == "01-Projects/Automation/devlog.md"

    def test_vault_edit(self):
        result = extract_file_path(
            "mcp__obsidian-pkm__vault_edit",
            {"path": "some/note.md", "old_string": "a", "new_string": "b"},
        )
        assert result == "some/note.md"

    def test_unrelated_tool(self):
        result = extract_file_path(
            "mcp__obsidian-pkm__vault_search", {"query": "test"},
        )
        assert result is None

    def test_non_mcp_tool(self):
        result = extract_file_path("Read", {"path": "/tmp/foo"})
        assert result is None

    def test_missing_path_key(self):
        result = extract_file_path("mcp__obsidian-pkm__vault_write", {})
        assert result is None
