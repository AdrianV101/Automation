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


import textwrap
from automation_daemon.clippings_state import parse_clipping, clipping_key


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


def test_parse_clipping_splits_frontmatter_and_body(tmp_path):
    p = _write(tmp_path, "c.md", """\
        ---
        title: "Hello"
        source: "https://x.com/a"
        tags:
          - clippings
        ---
        Body line one.
        """)
    fm, body = parse_clipping(p)
    assert fm["title"] == "Hello"
    assert fm["source"] == "https://x.com/a"
    assert body.strip() == "Body line one."


def test_parse_clipping_no_frontmatter(tmp_path):
    p = _write(tmp_path, "c.md", "just body, no frontmatter\n")
    fm, body = parse_clipping(p)
    assert fm == {}
    assert body.strip() == "just body, no frontmatter"


def test_clipping_key_prefers_source_url(tmp_path):
    assert clipping_key({"source": "https://X.com/a/?utm_source=z"}, "body") == "url:https://x.com/a"


def test_clipping_key_falls_back_to_content_hash():
    k = clipping_key({"source": ""}, "abc")
    assert k.startswith("hash:")
    assert k == clipping_key({}, "abc")  # same body → same key
    assert k != clipping_key({}, "abd")
