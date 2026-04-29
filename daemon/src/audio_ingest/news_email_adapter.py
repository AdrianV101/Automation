"""Newsletter email adapter for news@veraz.dev capture.

Lives in the daemon (not libs/email-ingest/) because it depends on vault
layout and Telegram conventions. Sibling to plaud_email_adapter.py.
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime

from bs4 import BeautifulSoup
from email_ingest import MalformedEmailError, ParsedEmail
from markdownify import markdownify as _markdownify

log = logging.getLogger(__name__)


_SLUG_NONALPHA = re.compile(r"[^a-z0-9]+")
_VIEW_IN_BROWSER_RE = re.compile(
    r"^\s*\[?[Vv]iew[^\]]*?(in[^\]]*?browser|on[^\]]*?web)[^\]]*?\]?(\([^)]*\))?\s*$",
)


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


def _strip_html_noise(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["style", "script"]):
        tag.decompose()
    for img in soup.find_all("img"):
        try:
            w = int(img.get("width", "0") or "0")
            h = int(img.get("height", "0") or "0")
        except ValueError:
            w, h = 0, 0
        if w == 1 or h == 1:
            img.decompose()
    return str(soup)


def _strip_leading_view_in_browser(md: str) -> str:
    lines = md.splitlines()
    while lines and (not lines[0].strip() or _VIEW_IN_BROWSER_RE.match(lines[0])):
        lines.pop(0)
    return "\n".join(lines)


def _collapse_blank_lines(md: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", md)


@dataclass(frozen=True)
class NewsNotePayload:
    message_id: str
    sender_display: str   # human-readable name, falling back to domain
    sender_address: str   # full email address
    subject: str
    received_at: datetime # always tz-aware
    html_body: str | None
    text_body: str | None


def _parse_sender(from_header: str) -> tuple[str, str]:
    display, address = parseaddr(from_header or "")
    if not address:
        return ("", "")
    if not display:
        domain = address.split("@", 1)[-1] if "@" in address else address
        return (domain, address)
    return (display, address)


def _parse_received_at(date_header: str) -> datetime:
    try:
        dt = parsedate_to_datetime(date_header or "")
    except (TypeError, ValueError):
        dt = None
    if dt is None:
        # Real production mail always has a Date header; this branch exists
        # only so the parser is total.
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def email_to_news_note(parsed: ParsedEmail) -> NewsNotePayload:
    headers = parsed.headers
    display, address = _parse_sender(headers.get("From") or "")
    return NewsNotePayload(
        message_id=parsed.message_id,
        sender_display=display,
        sender_address=address,
        subject=headers.get("Subject") or "",
        received_at=_parse_received_at(headers.get("Date") or ""),
        html_body=parsed.html_body,
        text_body=parsed.text_body,
    )


def render_body(parsed: ParsedEmail) -> str:
    """Render the email body to markdown. Prefer text/html (best fidelity for
    modern newsletter senders); fall back to text/plain when no HTML part
    exists. Raises MalformedEmailError when both parts are missing or empty."""
    if parsed.html_body and parsed.html_body.strip():
        cleaned_html = _strip_html_noise(parsed.html_body)
        md = _markdownify(cleaned_html, heading_style="ATX")
        md = _strip_leading_view_in_browser(md)
        return _collapse_blank_lines(md).strip()
    if parsed.text_body and parsed.text_body.strip():
        return parsed.text_body.strip()
    raise MalformedEmailError(
        f"email {parsed.message_id} has no usable body (html or text/plain)",
    )
