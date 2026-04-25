import os

import pytest

from telegram_interface.types import BotConfig

os.environ.setdefault("OBSIDIAN_MCP_SERVER_PATH", "/tmp/test-obsidian-mcp/index.js")


@pytest.fixture
def bot_config():
    return BotConfig(bot_token="test_bot_token", chat_id="12345")
