"""CLI subcommands."""
from __future__ import annotations

import argparse
import asyncio
import sys

from .config import DaemonConfig


def _plaud_ingest(args: argparse.Namespace, config: DaemonConfig) -> None:
    from . import plaud_mcp_ingest

    code = asyncio.run(plaud_mcp_ingest.run_manual_ingest(
        config, args.file_id, force=args.force, extract=args.extract,
    ))
    sys.exit(code)


def add_subparsers(subparsers: argparse._SubParsersAction) -> None:
    """Register CLI subcommands."""
    plaud = subparsers.add_parser(
        "plaud-ingest", help="Ingest one Plaud recording by file id via the Plaud MCP server",
    )
    plaud.add_argument("file_id", help="Plaud file id (f_... or of_...)")
    plaud.add_argument(
        "--force", action="store_true",
        help="Rewrite the transcript even if the recording was already ingested",
    )
    plaud.add_argument(
        "--extract", action="store_true",
        help="With --force, also re-run extraction (may duplicate routed notes)",
    )
    plaud.set_defaults(func=_plaud_ingest)
