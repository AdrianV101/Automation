from automation_daemon.clippings_state import normalize_url


def test_normalize_lowercases_scheme_and_host():
    assert normalize_url("HTTPS://Example.COM/Path") == "https://example.com/Path"


def test_normalize_strips_fragment_and_trailing_slash():
    assert normalize_url("https://x.com/a/?") == "https://x.com/a"
    assert normalize_url("https://x.com/a#frag") == "https://x.com/a"


def test_normalize_strips_tracking_params_keeps_meaningful_query():
    assert (
        normalize_url("https://x.com/p?utm_source=t&fbclid=9&id=42&gclid=z")
        == "https://x.com/p?id=42"
    )


def test_normalize_drops_default_ports():
    assert normalize_url("https://x.com:443/p") == "https://x.com/p"
    assert normalize_url("http://x.com:80/p") == "http://x.com/p"


def test_normalize_empty_returns_empty():
    assert normalize_url("") == ""
    assert normalize_url("   ") == ""
