from __future__ import annotations

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
