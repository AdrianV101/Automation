"""Newsletter email adapter for news@veraz.dev capture.

Lives in the daemon (not libs/email-ingest/) because it depends on vault
layout and Telegram conventions. Sibling to plaud_email_adapter.py.
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata

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
