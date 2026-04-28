"""Test that the daemon-commands factory builds CommandConfigs from the vault."""
from pathlib import Path

from audio_ingest.command_config import build_daemon_commands


def test_factory_returns_four_commands(tmp_path: Path):
    cmds = build_daemon_commands(tmp_path)
    names = [c.name for c in cmds]
    assert set(names) == {"note", "ask", "task", "chat"}
    assert len(names) == 4


def test_factory_injects_oneliner_into_each_prompt(tmp_path: Path):
    folder = tmp_path / "03-Resources" / "People"
    folder.mkdir(parents=True)
    (folder / "Adrian Verhoosel Azpiroz.md").write_text(
        "---\ntype: person\nrelationship: \"self\"\ndescription: \"x\"\ntags: [person]\n---\n",
        encoding="utf-8",
    )
    cmds = build_daemon_commands(tmp_path)
    for c in cmds:
        assert "Adrian Verhoosel Azpiroz (self)" in c.system_prompt
