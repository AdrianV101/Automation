import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Set before any automation_daemon imports so MCP_SERVER_PATH is populated at import time.
os.environ.setdefault("OBSIDIAN_MCP_SERVER_PATH", "/tmp/test-obsidian-mcp/index.js")

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_dotenv(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neutralise ``DaemonConfig.from_env``'s ``load_dotenv()`` call.

    ``from_env`` calls ``load_dotenv()`` with no path; python-dotenv
    discovers ``daemon/.env`` (searching upward from the package dir, or
    cwd under a REPL/debugger). It defaults to ``override=False``, so it
    sets a ``.env`` key only when that key is *absent* from ``os.environ``.
    That is exactly why the ``*_defaults_when_unset`` config tests are
    non-hermetic: they leave the optional ``*_ENABLED`` vars unset, so the
    real ``.env`` fills them in and the "disabled by default" assertions
    fail (they pass only on a checkout without a ``.env``, e.g. fresh CI).

    Tests that set a var via ``monkeypatch.setenv`` already have it in
    ``os.environ``, so real ``load_dotenv`` would skip it anyway — this
    no-op is belt-and-braces for them and the load-bearing fix for the
    unset/``delenv`` case.

    Opt out with ``@pytest.mark.real_dotenv`` for tests that must exercise
    actual ``.env``-file parsing (e.g. the ``from_env(env_file=...)`` path).
    """
    if request.node.get_closest_marker("real_dotenv"):
        return
    monkeypatch.setattr("automation_daemon.config.load_dotenv", lambda *a, **k: False)


@pytest.fixture
def sample_plaud_raw() -> dict:
    return json.loads((FIXTURES_DIR / "sample_plaud_transcript.json").read_text())


# ---------------------------------------------------------------------------
# Shared mock helpers for Agent SDK tests
# ---------------------------------------------------------------------------


class MockAssistantMessage:
    """Sentinel class for isinstance checks in agent SDK mocks."""


class MockTextBlock:
    """Sentinel class for isinstance checks in agent SDK mocks."""


class MockToolUseBlock:
    """Sentinel class for isinstance checks in agent SDK mocks."""


def make_assistant_message(text_blocks=None, tool_blocks=None):
    """Create a mock AssistantMessage with text and tool-use blocks."""
    content = []
    for text in (text_blocks or []):
        block = MagicMock()
        block.text = text
        block.__class__ = MockTextBlock
        content.append(block)
    for name, input_data in (tool_blocks or []):
        block = MagicMock()
        block.name = name
        block.input = input_data
        block.__class__ = MockToolUseBlock
        content.append(block)
    msg = MagicMock()
    msg.content = content
    msg.__class__ = MockAssistantMessage
    return msg


class MockUserMessage:
    """Sentinel class for isinstance checks in agent SDK mocks."""


class MockToolResultBlock:
    """Sentinel class for isinstance checks in agent SDK mocks."""


class MockResultMessage:
    """Sentinel class for isinstance checks in agent SDK mocks."""


class MockStreamEvent:
    """Sentinel class for isinstance checks in agent SDK mocks."""


def make_user_message(tool_results=None):
    """Create a mock UserMessage with ToolResultBlocks.

    tool_results: list of (tool_use_id, content_str) tuples.
    """
    content = []
    for tool_use_id, result_content in (tool_results or []):
        block = MagicMock()
        block.tool_use_id = tool_use_id
        block.content = result_content
        block.__class__ = MockToolResultBlock
        content.append(block)
    msg = MagicMock()
    msg.content = content
    msg.__class__ = MockUserMessage
    return msg


def make_result_message(num_turns=1, total_cost_usd=0.05, session_id="sess-123"):
    """Create a mock ResultMessage."""
    msg = MagicMock()
    msg.num_turns = num_turns
    msg.total_cost_usd = total_cost_usd
    msg.session_id = session_id
    msg.__class__ = MockResultMessage
    return msg
