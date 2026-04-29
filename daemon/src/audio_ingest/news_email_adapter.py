"""Newsletter email adapter for news@veraz.dev capture.

Lives in the daemon (not libs/email-ingest/) because it depends on vault
layout and Telegram conventions. Sibling to plaud_email_adapter.py.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata


_SLUG_NONALPHA = re.compile(r"[^a-z0-9]+")


def slugify_subject(subject: str, *, message_id: str) -> str:
    """Filename-safe slug from an email subject, with a 6-char Message-ID hash
    appended so distinct emails that happen to share a subject line still
    produce distinct filenames."""
    normalised = unicodedata.normalize("NFKD", subject)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    cleaned = _SLUG_NONALPHA.sub("-", ascii_only.lower()).strip("-")
    if not cleaned:
        cleaned = "untitled"
    truncated = cleaned[:60].rstrip("-")
    digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:6]
    return f"{truncated}-{digest}"
