from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import timezone
from pathlib import Path
from typing import Any

import yaml

from .item import NewsItem

_SLUG_NONALPHA = re.compile(r"[^a-z0-9]+")


def slugify_subject(subject: str, *, message_id: str) -> str:
    """Hash suffix prevents collisions across items sharing a subject."""
    normalised = unicodedata.normalize("NFKD", subject)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    cleaned = _SLUG_NONALPHA.sub("-", ascii_only.lower()).strip("-")
    if not cleaned:
        cleaned = "untitled"
    truncated = cleaned[:60].rstrip("-")
    digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:6]
    return f"{truncated}-{digest}"


def _frontmatter(item: NewsItem) -> str:
    received_utc = item.received_at.astimezone(timezone.utc)
    data: dict[str, Any] = {
        "type": "news-item",
        "created": received_utc.date().isoformat(),
        "received-at": received_utc.isoformat(),
        "source": item.source,
        "source-type": item.source_type,
        "source-address": item.source_address,
        "subject": item.subject,
        "message-id": item.message_id,
    }
    # Extras land between the canonical fields and tags so a caller with
    # extra={} produces byte-identical output to a writer without extras
    # support. Sorted so callers building extras from differently-ordered
    # dict literals still get deterministic frontmatter (matters for the
    # golden-file regression test under multiple Python versions).
    for key in sorted(item.extra):
        data[key] = item.extra[key]
    data["tags"] = ["news", f"source-{item.source_type}"]
    return yaml.safe_dump(
        data, sort_keys=False, allow_unicode=True, default_flow_style=False,
    )


def write_news_item(item: NewsItem, vault_root: Path) -> Path:
    received_utc = item.received_at.astimezone(timezone.utc)
    date_folder = received_utc.date().isoformat()
    slug = slugify_subject(item.subject, message_id=item.message_id)
    folder = vault_root / "00-Inbox" / "news" / date_folder
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{slug}.md"
    content = f"---\n{_frontmatter(item)}---\n\n{item.body_md.rstrip()}\n"
    path.write_text(content, encoding="utf-8")
    return path
