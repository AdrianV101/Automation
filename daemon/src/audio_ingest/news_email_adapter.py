"""Newsletter email adapter — vault + Telegram capture for the news folder."""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

import yaml
from bs4 import BeautifulSoup
from email_ingest import (
    MalformedEmailError, NewsIngestStateDB, ParsedEmail, parse_email,
)
from markdownify import markdownify as _markdownify

TelegramNotifier = Callable[..., Awaitable[None]]
_PREVIEW_CHARS = 200

log = logging.getLogger(__name__)


_SLUG_NONALPHA = re.compile(r"[^a-z0-9]+")
_VIEW_IN_BROWSER_RE = re.compile(
    r"^\s*\[?[Vv]iew[^\]]*?(in[^\]]*?browser|on[^\]]*?web)[^\]]*?\]?(\([^)]*\))?\s*$",
)


def slugify_subject(subject: str, *, message_id: str) -> str:
    """Hash suffix prevents collisions across emails sharing a subject."""
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
        # Fallback to "now" so a missing Date doesn't bury the note under 1970-01-01/.
        log.warning("Unparseable Date header %r — using current UTC time", date_header)
        return datetime.now(tz=timezone.utc)
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


def _frontmatter(payload: NewsNotePayload) -> str:
    received_utc = payload.received_at.astimezone(timezone.utc)
    data = {
        "type": "news-item",
        "created": received_utc.date().isoformat(),
        "received-at": received_utc.isoformat(),
        "source": payload.sender_display,
        "source-type": "newsletter",
        "source-address": payload.sender_address,
        "subject": payload.subject,
        "message-id": payload.message_id,
        "tags": ["news", "source-newsletter"],
    }
    return yaml.safe_dump(
        data, sort_keys=False, allow_unicode=True, default_flow_style=False,
    )


def write_news_note(
    payload: NewsNotePayload,
    *,
    body_md: str,
    vault_root: Path,
) -> Path:
    date_folder = payload.received_at.astimezone(timezone.utc).date().isoformat()
    slug = slugify_subject(payload.subject, message_id=payload.message_id)
    folder = vault_root / "00-Inbox" / "news" / date_folder
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{slug}.md"
    content = f"---\n{_frontmatter(payload)}---\n\n{body_md.rstrip()}\n"
    path.write_text(content, encoding="utf-8")
    return path


def _build_telegram_message(
    payload: NewsNotePayload, body_md: str, vault_path: Path,
) -> str:
    preview_lines = [line.strip() for line in body_md.splitlines() if line.strip()]
    text_preview = " ".join(preview_lines)[:_PREVIEW_CHARS]
    return (
        f"📰 {payload.sender_display}\n"
        f"{payload.subject}\n\n"
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
        payload = email_to_news_note(parsed)
        body_md = render_body(parsed)
        vault_path = write_news_note(
            payload, body_md=body_md, vault_root=vault_root,
        )
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
        log.exception(
            "Vault note %s written but DB update failed for %s",
            rel_vault_path, message_id,
        )
        await _safe_notify(
            telegram_notifier, news_topic_id,
            f"⚠️ News capture partial — manual fix required\n\n"
            f"Vault note written but DB status update failed.\n"
            f"From: {payload.sender_display}\nSubject: {payload.subject}\n"
            f"Path: {rel_vault_path}\nError: {exc}\n\n"
            f"Row is stuck at 'received' — delete from news_ingest_events to re-process, "
            f"or manually update status='written'.",
        )
        return

    msg = _build_telegram_message(payload, body_md, rel_vault_path)
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
