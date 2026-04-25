from __future__ import annotations

from email_ingest.auth import DkimFail, DkimPass, verify_dkim
from email_ingest.mime import parse_email

TRUSTED = "mail.protonmail.ch"


def test_real_plaud_email_passes_dkim_for_plaud_ai(real_01: bytes) -> None:
    parsed = parse_email(real_01)
    result = verify_dkim(parsed, required_domain="plaud.ai", trusted_authserv_id=TRUSTED)
    assert isinstance(result, DkimPass)
    assert result.signing_domain == "plaud.ai"
    assert result.passed is True


def test_dkim_fail_header_is_rejected(synth_dkim_fail: bytes) -> None:
    parsed = parse_email(synth_dkim_fail)
    result = verify_dkim(parsed, required_domain="plaud.ai", trusted_authserv_id=TRUSTED)
    assert isinstance(result, DkimFail)
    assert "fail" in result.reason.lower() or "no dkim=pass" in result.reason.lower()


def test_missing_dkim_header_is_rejected(synth_no_dkim: bytes) -> None:
    parsed = parse_email(synth_no_dkim)
    result = verify_dkim(parsed, required_domain="plaud.ai", trusted_authserv_id=TRUSTED)
    assert isinstance(result, DkimFail)
    assert "no authentication-results" in result.reason.lower()


def test_wrong_domain_is_rejected(real_01: bytes) -> None:
    parsed = parse_email(real_01)
    result = verify_dkim(parsed, required_domain="not-plaud.com", trusted_authserv_id=TRUSTED)
    assert isinstance(result, DkimFail)
    assert result.signing_domain == "plaud.ai"


def _synth_email_with_headers(extra_headers: list[str]) -> bytes:
    header_block = "\r\n".join(extra_headers)
    return (
        f"From: test@example.com\r\n"
        f"To: recipient@example.com\r\n"
        f"Subject: Test\r\n"
        f"Message-ID: <synth@example.com>\r\n"
        f"{header_block}\r\n"
        f"\r\n"
        f"body\r\n"
    ).encode()


def test_forged_ar_from_untrusted_mta_is_rejected() -> None:
    # Attacker injects their own Authentication-Results header claiming
    # dkim=pass d=plaud.ai. Our trusted MTA never signed off on this.
    raw = _synth_email_with_headers([
        "Authentication-Results: attacker.example; dkim=pass d=plaud.ai",
    ])
    parsed = parse_email(raw)
    result = verify_dkim(parsed, required_domain="plaud.ai", trusted_authserv_id=TRUSTED)
    assert isinstance(result, DkimFail)
    assert "trusted authserv-id" in result.reason


def test_untrusted_ar_is_ignored_when_trusted_one_exists() -> None:
    # Multiple AR headers: only the trusted MTA's decision counts.
    raw = _synth_email_with_headers([
        "Authentication-Results: attacker.example; dkim=pass d=plaud.ai",
        f"Authentication-Results: {TRUSTED}; dkim=fail d=plaud.ai",
    ])
    parsed = parse_email(raw)
    result = verify_dkim(parsed, required_domain="plaud.ai", trusted_authserv_id=TRUSTED)
    assert isinstance(result, DkimFail)


def test_multiple_trusted_ar_headers_any_passing_domain_suffices() -> None:
    # Real Plaud emails carry 5 AR headers, only one of which is the dkim
    # check for plaud.ai. All must be iterated, not just the last.
    raw = _synth_email_with_headers([
        f"Authentication-Results: {TRUSTED}; spf=pass smtp.mailfrom=plaud.ai",
        f"Authentication-Results: {TRUSTED}; dmarc=pass",
        f"Authentication-Results: {TRUSTED}; dkim=pass (2048-bit key) header.d=plaud.ai",
    ])
    parsed = parse_email(raw)
    result = verify_dkim(parsed, required_domain="plaud.ai", trusted_authserv_id=TRUSTED)
    assert isinstance(result, DkimPass)
    assert result.signing_domain == "plaud.ai"


def test_authserv_id_match_is_case_insensitive() -> None:
    raw = _synth_email_with_headers([
        "Authentication-Results: MAIL.PROTONMAIL.CH; dkim=pass d=plaud.ai",
    ])
    parsed = parse_email(raw)
    result = verify_dkim(parsed, required_domain="plaud.ai", trusted_authserv_id=TRUSTED)
    assert isinstance(result, DkimPass)
