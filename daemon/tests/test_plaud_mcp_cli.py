import argparse
from unittest.mock import AsyncMock, patch

import pytest

from automation_daemon.cli import add_subparsers
from automation_daemon.config import DaemonConfig


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="automation-daemon")
    subparsers = parser.add_subparsers(dest="command")
    add_subparsers(subparsers)
    return parser


def test_parses_plaud_ingest_with_flags() -> None:
    args = _parser().parse_args(["plaud-ingest", "f_x", "--force", "--extract"])
    assert args.command == "plaud-ingest"
    assert args.file_id == "f_x"
    assert args.force is True
    assert args.extract is True


def test_func_dispatches_to_run_manual_ingest(tmp_path) -> None:
    args = _parser().parse_args(["plaud-ingest", "f_x", "--force"])
    cfg = DaemonConfig(pkm_vault_path=tmp_path)
    fake = AsyncMock(return_value=0)
    with patch("automation_daemon.plaud_mcp_ingest.run_manual_ingest", new=fake):
        with pytest.raises(SystemExit) as excinfo:
            args.func(args, cfg)
    assert excinfo.value.code == 0
    fake.assert_awaited_once_with(cfg, "f_x", force=True, extract=False)
