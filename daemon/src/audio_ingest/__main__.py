import argparse
import asyncio
import logging

from .cli import add_subparsers
from .config import DaemonConfig
from .orchestrator import run_daemon

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main() -> None:
    parser = argparse.ArgumentParser(prog="audio-ingest")
    subparsers = parser.add_subparsers(dest="command")
    add_subparsers(subparsers)

    args = parser.parse_args()
    config = DaemonConfig.from_env()

    if args.command is None:
        # No subcommand = run daemon (backward compatible)
        asyncio.run(run_daemon(config))
    else:
        args.func(args, config)


if __name__ == "__main__":
    main()
