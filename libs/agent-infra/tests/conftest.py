import os
from unittest.mock import MagicMock

# Set before any agent_infra imports so MCP_SERVER_PATH is populated at import time.
os.environ.setdefault("OBSIDIAN_MCP_SERVER_PATH", "/tmp/test-obsidian-mcp/index.js")


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


def make_user_message(tool_results=None):
    """Create a mock UserMessage with ToolResultBlocks.

    tool_results: list of (tool_use_id, content_str) or
                  (tool_use_id, content_str, is_error) tuples.
    """
    content = []
    for entry in (tool_results or []):
        if len(entry) == 3:
            tool_use_id, result_content, is_error = entry
        else:
            tool_use_id, result_content = entry
            is_error = False
        block = MagicMock()
        block.tool_use_id = tool_use_id
        block.content = result_content
        block.is_error = is_error
        block.__class__ = MockToolResultBlock
        content.append(block)
    msg = MagicMock()
    msg.content = content
    msg.__class__ = MockUserMessage
    return msg


def make_result_message(num_turns=1, total_cost_usd=0.05, session_id="sess-123", stop_reason=None):
    """Create a mock ResultMessage."""
    msg = MagicMock()
    msg.num_turns = num_turns
    msg.total_cost_usd = total_cost_usd
    msg.session_id = session_id
    msg.stop_reason = stop_reason
    msg.__class__ = MockResultMessage
    return msg
