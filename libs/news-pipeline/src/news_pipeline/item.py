from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, get_args

SourceType = Literal["newsletter", "hacker-news", "financial-times", "x-twitter"]
SOURCE_TYPES: frozenset[str] = frozenset(get_args(SourceType))


@dataclass(frozen=True)
class NewsItem:
    message_id: str
    source: str
    source_type: SourceType
    source_address: str
    subject: str
    received_at: datetime
    body_md: str
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.received_at.tzinfo is None:
            raise ValueError("NewsItem.received_at must be timezone-aware")
        if self.source_type not in SOURCE_TYPES:
            raise ValueError(
                f"unknown source_type {self.source_type!r}; "
                f"expected one of {sorted(SOURCE_TYPES)}",
            )
        # message_id = "" makes every untitled item collide on slug
        # `untitled-<hash("")>`. body_md = "" produces a frontmatter-only note.
        # Empty subject/source/source_address are tolerated — real emails ship them.
        for name in ("message_id", "body_md"):
            if not getattr(self, name):
                raise ValueError(f"NewsItem.{name} must be non-empty")
        for reserved in _RESERVED_FRONTMATTER_KEYS:
            if reserved in self.extra:
                raise ValueError(
                    f"extra key {reserved!r} collides with a canonical "
                    f"frontmatter field; use a source-specific name instead",
                )


_RESERVED_FRONTMATTER_KEYS: frozenset[str] = frozenset({
    "type", "created", "received-at", "source", "source-type",
    "source-address", "subject", "message-id", "tags",
})
