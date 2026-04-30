"""Newsletter email adapter — vault + Telegram capture for the news folder.

Email-transport-specific layer. Body rendering and sender extraction live here;
the cross-source NewsItem contract and writer live in `news_pipeline`.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from bs4 import BeautifulSoup
from email_ingest import (
    MalformedEmailError, NewsIngestStateDB, ParsedEmail, parse_email,
)
from markdownify import markdownify as _markdownify
from news_pipeline import NewsItem, SourceType, write_news_item

TelegramNotifier = Callable[..., Awaitable[None]]
_PREVIEW_CHARS = 200

# Sender registered-domain → source_type. Subdomain matches walk to the
# parent (`firstft@email.ft.com` resolves via `ft.com`). Anything not in
# this map falls through to `"newsletter"`.
_DOMAIN_SOURCE_TYPES: dict[str, SourceType] = {
    "ft.com": "financial-times",
}

log = logging.getLogger(__name__)


_VIEW_IN_BROWSER_RE = re.compile(
    r"^\s*\[?[Vv]iew[^\]]*?(in[^\]]*?browser|on[^\]]*?web)[^\]]*?\]?(\([^)]*\))?\s*$",
)


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


def _classify_source_type(address: str) -> SourceType:
    if "@" not in address:
        return "newsletter"
    domain = address.rsplit("@", 1)[-1].lower()
    while "." in domain:
        if domain in _DOMAIN_SOURCE_TYPES:
            return _DOMAIN_SOURCE_TYPES[domain]
        domain = domain.split(".", 1)[1]
    return _DOMAIN_SOURCE_TYPES.get(domain, "newsletter")


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
        # Fallback to "now" so a missing Date doesn't bury the note under 1970-01-01/.
        log.warning("Unparseable Date header %r — using current UTC time", date_header)
        return datetime.now(tz=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def render_body(parsed: ParsedEmail) -> str:
    """HTML→MD when present (Substack/Beehiiv plaintext is usually a stub); plaintext otherwise."""
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


def email_to_news_item(parsed: ParsedEmail, body_md: str) -> NewsItem:
    headers = parsed.headers
    display, address = _parse_sender(headers.get("From") or "")
    return NewsItem(
        message_id=parsed.message_id,
        source=display,
        source_type=_classify_source_type(address),
        source_address=address,
        subject=headers.get("Subject") or "",
        received_at=_parse_received_at(headers.get("Date") or ""),
        body_md=body_md,
    )


def _build_telegram_message(item: NewsItem, vault_path: Path) -> str:
    preview_lines = [line.strip() for line in item.body_md.splitlines() if line.strip()]
    text_preview = " ".join(preview_lines)[:_PREVIEW_CHARS]
    return (
        f"📰 {item.source}\n"
        f"{item.subject}\n\n"
        f"{text_preview}\n\n"
        f"🔗 {vault_path}"
    )


async def _safe_notify(
    notifier: TelegramNotifier, topic_id: int | None, text: str,
) -> None:
    try:
        if topic_id is None:
            await notifier(text)
        else:
            await notifier(text, thread_id=topic_id)
    except Exception:
        log.exception("News Telegram notify failed; vault note already persisted")


async def handle_news_email(
    uid: int,
    raw: bytes,
    *,
    db: NewsIngestStateDB,
    vault_root: Path,
    telegram_notifier: TelegramNotifier,
    news_topic_id: int | None,
) -> None:
    parsed = parse_email(raw)
    message_id = parsed.message_id
    if not message_id:
        log.warning("News email at UID %d has no Message-ID — skipping", uid)
        return

    if await db.is_processed(message_id):
        log.debug("News email %s already processed — skipping", message_id)
        return

    sender_header = parsed.headers.get("From")
    subject_header = parsed.headers.get("Subject")
    # Insert first so a crash leaves a 'received' row the catch-all can transition to 'failed'.
    await db.insert_event(
        message_id=message_id, uid=uid,
        sender=sender_header, subject=subject_header,
    )

    try:
        body_md = render_body(parsed)
        item = email_to_news_item(parsed, body_md)
        vault_path = write_news_item(item, vault_root)
    except Exception as exc:
        log.exception("Failed to capture news email %s", message_id)
        await _mark_failed(
            db, message_id, exc,
            telegram_notifier=telegram_notifier, news_topic_id=news_topic_id,
            sender=sender_header, subject=subject_header,
        )
        return

    rel_vault_path = vault_path.relative_to(vault_root)

    # Vault is the durable record; if status stays 'received' dedup hides the note forever.
    try:
        await db.update_status(
            message_id, "written", vault_note_path=str(rel_vault_path),
        )
    except Exception as exc:
        # Distinct error tag so a log search can find the stuck row even if
        # the operator's Telegram alert below ALSO fails (this is the only
        # signal that a row is dedup-skipped forever).
        log.exception(
            "NEWS_CAPTURE_PARTIAL message_id=%s vault_path=%s "
            "(vault written, DB stuck at 'received', dedup will hide it)",
            message_id, rel_vault_path,
        )
        await _safe_notify(
            telegram_notifier, news_topic_id,
            f"⚠️ News capture partial — manual fix required\n\n"
            f"Vault note written but DB status update failed.\n"
            f"From: {item.source}\nSubject: {item.subject}\n"
            f"Path: {rel_vault_path}\nError: {exc}\n\n"
            f"Row is stuck at 'received' — delete from news_ingest_events to re-process, "
            f"or manually update status='written'.",
        )
        return

    msg = _build_telegram_message(item, rel_vault_path)
    await _safe_notify(telegram_notifier, news_topic_id, msg)


async def _mark_failed(
    db: NewsIngestStateDB,
    message_id: str,
    exc: BaseException,
    *,
    telegram_notifier: TelegramNotifier,
    news_topic_id: int | None,
    sender: str | None,
    subject: str | None,
) -> None:
    db_update_failed: Exception | None = None
    try:
        await db.update_status(message_id, "failed", error=str(exc))
    except Exception as db_exc:
        # If we can't even mark the row failed, the row is stuck at 'received' and
        # dedupe will skip it forever. The Telegram alert MUST surface this so the
        # operator can manually delete the row.
        log.exception("Failed to mark news row %s failed", message_id)
        db_update_failed = db_exc

    if db_update_failed is None:
        text = (
            f"⚠️ News capture failed\n\n"
            f"From: {sender}\nSubject: {subject}\n"
            f"Message-ID: {message_id}\nError: {exc}"
        )
    else:
        text = (
            f"⚠️ News capture failed AND state DB update failed\n\n"
            f"From: {sender}\nSubject: {subject}\n"
            f"Message-ID: {message_id}\n"
            f"Capture error: {exc}\n"
            f"DB error: {db_update_failed}\n\n"
            f"Row is stuck at 'received' — delete from news_ingest_events to re-process."
        )
    await _safe_notify(telegram_notifier, news_topic_id, text)
