from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
import yaml
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_TRACKING_PREFIXES = ("utm_",)
_TRACKING_EXACT = {"fbclid", "gclid", "gbraid", "wbraid", "ref", "ref_src", "mc_cid", "mc_eid"}
_DEFAULT_PORTS = {"http": "80", "https": "443"}


def _is_tracking(param: str) -> bool:
    return param in _TRACKING_EXACT or any(param.startswith(p) for p in _TRACKING_PREFIXES)


def normalize_url(url: str) -> str:
    """Canonicalize a clipping source URL for idempotency keying.

    Lowercases scheme+host, drops default ports, removes fragment and known
    tracking params, strips a trailing slash and empty query. Returns "" for
    blank input (caller falls back to a content hash).
    """
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    port = parts.port
    if port is not None and _DEFAULT_PORTS.get(scheme) != str(port):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if not _is_tracking(k)]
    )
    path = parts.path
    if path.endswith("/") and path != "/":
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, query, ""))


def parse_clipping(path: Path) -> tuple[dict[str, Any], str]:
    """Return (frontmatter dict, body) for a clipping markdown file.

    Frontmatter is the leading ``---\\n ... \\n---`` YAML block. Files
    without it yield ({}, full_text). Malformed YAML yields ({}, body)
    rather than raising — the adapter classifies empty-frontmatter
    clippings as ``failed`` downstream.
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        return {}, text
    raw_fm = parts[0][len("---"):]
    body = parts[1].lstrip("\n")
    try:
        loaded = yaml.safe_load(raw_fm)
    except yaml.YAMLError:
        return {}, body
    fm = loaded if isinstance(loaded, dict) else {}
    return fm, body


def clipping_key(frontmatter: dict[str, Any], body: str) -> str:
    """Idempotency key: normalized source URL, else sha256 of the body."""
    source = str(frontmatter.get("source") or "").strip()
    normalized = normalize_url(source)
    if normalized:
        return f"url:{normalized}"
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f"hash:{digest}"


# ---------------------------------------------------------------------------
# State DB
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clippings_ingest_events (
    url_key TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    original_filename TEXT,
    candidates_json TEXT,
    telegram_message_id INTEGER,
    routed_path TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    processed_at TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    d = dict(row)
    raw = d.get("candidates_json")
    d["candidates"] = json.loads(raw) if raw else []
    return d


class ClippingsStateDB:
    """Idempotency + clarification state for clipping ingestion.

    One row per clipping keyed by `clipping_key()`. Mirrors the
    fresh-connection-per-call aiosqlite pattern of HackerNewsStateDB.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.executescript(_SCHEMA)
            await db.commit()

    async def get(self, url_key: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM clippings_ingest_events WHERE url_key = ?", (url_key,),
            ) as cur:
                row = await cur.fetchone()
                return _row_to_dict(row) if row else None

    async def insert_pending(self, url_key: str, original_filename: str) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO clippings_ingest_events "
                "(url_key, status, original_filename, retry_count, processed_at) "
                "VALUES (?, 'pending', ?, 0, ?)",
                (url_key, original_filename, _now_iso()),
            )
            await db.commit()

    async def _set_status(self, url_key: str, status: str, **cols: Any) -> None:
        assignments = ["status = ?", "processed_at = ?"]
        values: list[Any] = [status, _now_iso()]
        for name, val in cols.items():
            assignments.append(f"{name} = ?")
            values.append(val)
        values.append(url_key)
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute(
                f"UPDATE clippings_ingest_events SET {', '.join(assignments)} WHERE url_key = ?",
                tuple(values),
            )
            await db.commit()
            if cur.rowcount == 0:
                raise RuntimeError(f"_set_status({status}) for {url_key!r} affected no rows")

    async def mark_routed(self, url_key: str, routed_path: str) -> None:
        await self._set_status(url_key, "routed", routed_path=routed_path)

    async def mark_skipped(self, url_key: str) -> None:
        await self._set_status(url_key, "skipped")

    async def mark_failed(self, url_key: str) -> None:
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute(
                "UPDATE clippings_ingest_events "
                "SET status = 'failed', retry_count = retry_count + 1, processed_at = ? "
                "WHERE url_key = ?",
                (_now_iso(), url_key),
            )
            await db.commit()
            if cur.rowcount == 0:
                raise RuntimeError(f"mark_failed for {url_key!r} affected no rows")

    async def set_pending_clarification(
        self, url_key: str, *, candidates: list[str], telegram_message_id: int | None,
    ) -> None:
        await self._set_status(
            url_key, "pending_clarification",
            candidates_json=json.dumps(candidates),
            telegram_message_id=telegram_message_id,
        )

    async def find_pending_clarification_by_message(
        self, telegram_message_id: int,
    ) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM clippings_ingest_events "
                "WHERE status = 'pending_clarification' AND telegram_message_id = ?",
                (telegram_message_id,),
            ) as cur:
                row = await cur.fetchone()
                return _row_to_dict(row) if row else None

    async def oldest_pending_clarification(self) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM clippings_ingest_events "
                "WHERE status = 'pending_clarification' ORDER BY processed_at ASC LIMIT 1",
            ) as cur:
                row = await cur.fetchone()
                return _row_to_dict(row) if row else None

    async def all_pending_clarifications(self) -> list[dict[str, Any]]:
        """Return all pending_clarification rows ordered by processed_at ASC."""
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM clippings_ingest_events "
                "WHERE status = 'pending_clarification' ORDER BY processed_at ASC",
            ) as cur:
                rows = await cur.fetchall()
                return [_row_to_dict(r) for r in rows]
