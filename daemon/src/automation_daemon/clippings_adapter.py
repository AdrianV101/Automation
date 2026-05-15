from __future__ import annotations

import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)


async def wait_until_stable(
    path: Path, *, settle_s: float, poll_s: float, timeout_s: float,
) -> bool:
    """True once (size, mtime) is unchanged for `settle_s`.

    Guards against partial Obsidian Sync writes. False if the file
    vanishes or never settles within `timeout_s`.
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    last: tuple[int, float] | None = None
    stable_since: float | None = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            st = path.stat()
        except FileNotFoundError:
            return False
        sig = (st.st_size, st.st_mtime)
        now = asyncio.get_event_loop().time()
        if sig == last:
            if stable_since is not None and (now - stable_since) >= settle_s:
                return True
        else:
            last = sig
            stable_since = now
        await asyncio.sleep(poll_s)
    return False
