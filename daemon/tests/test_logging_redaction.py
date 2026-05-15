"""Importing the daemon entrypoint must silence httpx/httpcore INFO logs.

httpx logs every request URL at INFO. The daemon's Telegram client uses
URLs of the form `api.telegram.org/bot<TOKEN>/...`, so any INFO-level
HTTP log leaks the bot token. Pinning these loggers to WARNING is a
small, durable redaction that survives library upgrades.
"""
import logging


def test_httpx_logger_is_warning_or_higher():
    import automation_daemon.__main__  # noqa: F401 — import for side effect
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_httpcore_logger_is_warning_or_higher():
    import automation_daemon.__main__  # noqa: F401
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
