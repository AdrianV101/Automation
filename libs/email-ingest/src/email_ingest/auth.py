from __future__ import annotations

import re
from dataclasses import dataclass

from .mime import ParsedEmail


# `DkimResult` is a tagged union of `DkimPass` | `DkimFail`. Both expose
# `.passed` and `.reason` so existing callers can keep the duck-typed
# boolean check while pattern-matching code gets exhaustive variants.
@dataclass(frozen=True)
class DkimPass:
    signing_domain: str
    authserv_id: str

    @property
    def passed(self) -> bool:
        return True

    @property
    def reason(self) -> str | None:
        return None


@dataclass(frozen=True)
class DkimFail:
    signing_domain: str | None
    reason: str

    @property
    def passed(self) -> bool:
        return False


DkimResult = DkimPass | DkimFail


_AUTHSERV_SPLIT = re.compile(r"\s*;\s*")
_DKIM_ENTRY = re.compile(
    r"dkim\s*=\s*(?P<status>\w+)(?:[^;]*?\bd\s*=\s*(?P<domain>[^\s;]+))?",
    re.IGNORECASE,
)


def _authserv_id(header_value: str) -> str:
    # Authentication-Results: authserv-id; method=result method=result ...
    # RFC 8601 §2.2 — authserv-id is the first token, ended by `;`.
    first, _sep, _rest = header_value.partition(";")
    return first.strip().lower()


def verify_dkim(
    msg: ParsedEmail,
    required_domain: str,
    trusted_authserv_id: str,
) -> DkimResult:
    # Scope verification to `Authentication-Results` headers written by the
    # trusted MTA only. Anything else can be forged by upstream relays.
    headers = msg.get_all("Authentication-Results")
    if not headers:
        return DkimFail(signing_domain=None, reason="no Authentication-Results header")

    trusted = [h for h in headers if _authserv_id(h) == trusted_authserv_id.lower()]
    if not trusted:
        seen = ", ".join(_authserv_id(h) or "?" for h in headers)
        return DkimFail(
            signing_domain=None,
            reason=f"no Authentication-Results from trusted authserv-id "
                   f"{trusted_authserv_id!r} (saw: {seen})",
        )

    first_passing_domain: str | None = None
    last_domain: str | None = None
    last_status: str | None = None

    for header_value in trusted:
        authserv = _authserv_id(header_value)
        for match in _DKIM_ENTRY.finditer(header_value):
            status = match.group("status").lower()
            domain = (match.group("domain") or "").lower()
            if status == "pass" and domain == required_domain.lower():
                return DkimPass(signing_domain=domain, authserv_id=authserv)
            if status == "pass" and domain and first_passing_domain is None:
                first_passing_domain = domain
            last_domain = domain or last_domain
            last_status = status

    reported_domain = first_passing_domain or last_domain
    detail = f" (last seen: dkim={last_status} d={last_domain})" if last_status else ""
    return DkimFail(
        signing_domain=reported_domain,
        reason=f"no dkim=pass for d={required_domain}{detail}",
    )
