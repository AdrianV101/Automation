from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

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
