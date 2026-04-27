import argparse
import asyncio
import logging
import os
import signal

import psutil

from .cli import add_subparsers
from .config import DaemonConfig
from .orchestrator import run_daemon

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

log = logging.getLogger(__name__)


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    def _request_shutdown(sig_name: str) -> None:
        log.info("Received %s, cancelling tasks", sig_name)
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_shutdown, sig.name)
        except NotImplementedError:
            # add_signal_handler isn't available on Windows; the daemon
            # only ships on Unix, so we skip rather than handle.
            pass


def _terminate_children(timeout_s: float = 5.0) -> None:
    """SIGTERM all descendant processes, escalating to SIGKILL after timeout.

    Called after the asyncio loop exits. Catches stuck Claude Code subprocesses
    (and their MCP server children) that the SDK didn't clean up.
    """
    me = psutil.Process(os.getpid())
    children = me.children(recursive=True)
    if not children:
        return
    log.info("Terminating %d child process(es) on shutdown", len(children))
    for c in children:
        try:
            c.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    gone, alive = psutil.wait_procs(children, timeout=timeout_s)
    for c in alive:
        try:
            c.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def main() -> None:
    # Become a process group leader so children inherit our pgid.
    # Best-effort: if the daemon is already a session leader (rare under
    # launchd) this is a no-op or raises PermissionError.
    try:
        os.setpgrp()
    except (PermissionError, OSError):
        pass

    parser = argparse.ArgumentParser(prog="audio-ingest")
    subparsers = parser.add_subparsers(dest="command")
    add_subparsers(subparsers)

    args = parser.parse_args()
    config = DaemonConfig.from_env()

    if args.command is not None:
        args.func(args, config)
        return

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        _install_signal_handlers(loop)
        try:
            loop.run_until_complete(run_daemon(config))
        except asyncio.CancelledError:
            log.info("Daemon cancelled, draining")
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()
        _terminate_children()


if __name__ == "__main__":
    main()
