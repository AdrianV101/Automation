from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class NewsSourceState(Protocol):
    """Listener-facing state contract every news source's state DB must satisfy.

    Per-source DBs keep their own dedupe-key shape (Message-ID, HN item_id,
    article URL, tweet ID) — this protocol unifies only the surface the
    listener layer touches. Names are source-agnostic; an IMAP-IDLE source's
    "checkpoint" is a UIDNEXT, an HN poller's is the largest-seen item id, an
    FT poller's is a since-timestamp.

    Implementations MUST make `record_processed` durable before returning —
    otherwise a crash before the next poll could re-emit items.
    """

    async def get_checkpoint(self) -> int: ...
    async def set_checkpoint(self, checkpoint: int) -> None: ...
    async def is_processed(self, key: str) -> bool: ...
    async def record_processed(self, key: str, vault_path: str) -> None: ...
