"""Tests for agent_infra.options — build_agent_options and DISALLOWED_NATIVE_TOOLS."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_infra import build_agent_options, DISALLOWED_NATIVE_TOOLS


class TestBuildAgentOptions:
    """Tests for build_agent_options."""

    def test_standard_config(self, tmp_path: Path) -> None:
        opts = build_agent_options("You are a test agent.", tmp_path)
        assert opts.system_prompt == "You are a test agent."
        assert opts.permission_mode == "bypassPermissions"
        assert opts.model == "claude-opus-4-6"
        assert opts.disallowed_tools == DISALLOWED_NATIVE_TOOLS
        assert opts.env == {"ANTHROPIC_API_KEY": ""}

    def test_mcp_server_config(self, tmp_path: Path) -> None:
        opts = build_agent_options("sys", tmp_path)
        mcp = opts.mcp_servers["obsidian-pkm"]
        assert mcp["type"] == "stdio"
        assert Path(mcp["command"]).name == "node"
        assert str(tmp_path) in mcp["env"]["VAULT_PATH"]

    def test_default_tools_empty(self, tmp_path: Path) -> None:
        """Default allowed_tools is [] — not domain-specific TOOLS_EXTRACTION."""
        opts = build_agent_options("sys", tmp_path)
        assert opts.allowed_tools == []

    def test_custom_tools_override(self, tmp_path: Path) -> None:
        tools = ["mcp__obsidian-pkm__vault_read", "mcp__obsidian-pkm__vault_write"]
        opts = build_agent_options("sys", tmp_path, allowed_tools=tools)
        assert opts.allowed_tools == tools

    def test_mcp_server_path_param_overrides_env(self, tmp_path: Path) -> None:
        custom_path = "/custom/path/to/index.js"
        opts = build_agent_options("sys", tmp_path, mcp_server_path=custom_path)
        mcp = opts.mcp_servers["obsidian-pkm"]
        assert mcp["args"] == [custom_path]

    def test_mcp_server_path_from_env(self, tmp_path: Path) -> None:
        """When no mcp_server_path param, falls back to env var."""
        opts = build_agent_options("sys", tmp_path)
        mcp = opts.mcp_servers["obsidian-pkm"]
        # conftest.py sets OBSIDIAN_MCP_SERVER_PATH to /tmp/test-obsidian-mcp/index.js
        assert mcp["args"] == [os.environ["OBSIDIAN_MCP_SERVER_PATH"]]

    def test_missing_mcp_server_path_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OBSIDIAN_MCP_SERVER_PATH", raising=False)
        with pytest.raises(RuntimeError, match="OBSIDIAN_MCP_SERVER_PATH"):
            build_agent_options("sys", tmp_path)

    def test_missing_node_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", "/nonexistent")
        with pytest.raises(RuntimeError, match="node"):
            build_agent_options("sys", tmp_path)

    def test_uses_absolute_node_path(self, tmp_path: Path) -> None:
        opts = build_agent_options("sys", tmp_path)
        mcp = opts.mcp_servers["obsidian-pkm"]
        assert Path(mcp["command"]).is_absolute()

    def test_custom_model(self, tmp_path: Path) -> None:
        opts = build_agent_options("sys", tmp_path, model="claude-sonnet-4-20250514")
        assert opts.model == "claude-sonnet-4-20250514"

    def test_max_turns_passed_through(self, tmp_path: Path) -> None:
        opts = build_agent_options("sys", tmp_path, max_turns=40)
        assert opts.max_turns == 40

    def test_max_turns_default_is_none(self, tmp_path: Path) -> None:
        opts = build_agent_options("sys", tmp_path)
        assert opts.max_turns is None

    def test_openai_api_key_included_when_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        opts = build_agent_options("sys", tmp_path)
        mcp = opts.mcp_servers["obsidian-pkm"]
        assert mcp["env"]["OPENAI_API_KEY"] == "sk-test-key"

    def test_openai_api_key_excluded_when_unset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        opts = build_agent_options("sys", tmp_path)
        mcp = opts.mcp_servers["obsidian-pkm"]
        assert "OPENAI_API_KEY" not in mcp["env"]


class TestDisallowedNativeTools:
    """Tests for DISALLOWED_NATIVE_TOOLS constant."""

    @pytest.mark.parametrize("tool", ["Edit", "Write", "Bash", "TodoWrite", "Task", "NotebookEdit", "Skill"])
    def test_contains_dangerous_tools(self, tool: str) -> None:
        assert tool in DISALLOWED_NATIVE_TOOLS

    def test_is_list(self) -> None:
        assert isinstance(DISALLOWED_NATIVE_TOOLS, list)
        assert len(DISALLOWED_NATIVE_TOOLS) == 7
