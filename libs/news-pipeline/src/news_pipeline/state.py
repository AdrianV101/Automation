from __future__ import annotations

from typing import Protocol


class NewsSourceState(Protocol):
    """Listener-facing state contract every news source's state DB must satisfy.

    Per-source DBs keep their own dedupe-key shape (Message-ID, HN item_id,
    article URL, tweet ID) — this protocol unifies only the surface the daemon's
    listener layer touches.
    """

    async def get_uidnext_checkpoint(self) -> int: ...
    async def set_uidnext_checkpoint(self, uidnext: int) -> None: ...
    async def is_processed(self, key: str) -> bool: ...
    async def record_processed(self, key: str, vault_path: str) -> None: ...
