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

# httpx/httpcore log every request URL at INFO, which leaks the Telegram bot
# token (embedded as a path component in api.telegram.org/bot<TOKEN>/...).
# Bump them to WARNING so credentials don't end up in log files or backups.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

log = logging.getLogger(__name__)


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop, root_task: asyncio.Task,
) -> None:
    # Cancel only the root task; structured cancellation unwinds from there.
    def _request_shutdown(sig_name: str) -> None:
        log.info("Received %s, cancelling root task", sig_name)
        if not root_task.done():
            root_task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_shutdown, sig.name)
        except NotImplementedError:
            log.warning(
                "add_signal_handler not supported for %s; "
                "graceful shutdown disabled for this signal",
                sig.name,
            )


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
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            try:
                name = c.name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                name = "<unknown>"
            log.warning(
                "AccessDenied terminating child pid=%d (%s); leaking",
                c.pid, name,
            )
    try:
        _gone, alive = psutil.wait_procs(children, timeout=timeout_s)
    except Exception:
        log.exception(
            "psutil.wait_procs raised; force-killing all children",
        )
        alive = children
    for c in alive:
        try:
            c.kill()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            try:
                name = c.name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                name = "<unknown>"
            log.warning(
                "AccessDenied killing child pid=%d (%s); leaking",
                c.pid, name,
            )


def main() -> None:
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
        root = loop.create_task(run_daemon(config))
        _install_signal_handlers(loop, root)
        try:
            loop.run_until_complete(root)
        except asyncio.CancelledError:
            log.info("Daemon cancelled, draining")
        except Exception:
            log.exception("Daemon exited with unexpected error")
            raise
    finally:
        try:
            loop.run_until_complete(
                asyncio.wait_for(loop.shutdown_asyncgens(), timeout=10.0)
            )
        except asyncio.TimeoutError:
            log.warning("shutdown_asyncgens timed out after 10s; proceeding to child reaper")
        except Exception:
            log.warning("shutdown_asyncgens raised during teardown", exc_info=True)
        finally:
            loop.close()
        _terminate_children()


if __name__ == "__main__":
    main()
