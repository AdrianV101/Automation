# news-pipeline

Shared contract for news-ingestion sources. Defines the `NewsItem` dataclass,
the `write_news_item` writer, the on-disk frontmatter schema, and the
`NewsSourceState` protocol every source's state DB must satisfy.

The sibling `audio_ingest.news_email_adapter` is the first consumer (newsletter
emails). Phase 2 sources (Hacker News, Financial Times, X/Twitter) plug in
against the same contract.

## Frontmatter schema

Every news item written via `write_news_item` produces exactly these keys, in
this order:

```yaml
---
type: news-item                               # always
created: '2026-04-29'                         # received_at.date() in UTC
received-at: '2026-04-29T08:14:32+00:00'      # received_at ISO-8601, tz-aware
source: <display name>                        # human-readable source label
source-type: newsletter | hacker-news | financial-times | x-twitter
source-address: <canonical pointer>           # email, URL, or @handle
subject: <title or headline>
message-id: <per-source unique id>            # email Message-ID, HN item id, etc.
<...source-specific extras...>                # from NewsItem.extra
tags:
- news
- source-<source-type>
---
```

Source-specific fields go in `NewsItem.extra` and are merged in between
`message-id` and `tags`. They flow into frontmatter unchanged. Examples:

| Source         | Suggested extras                                        |
| -------------- | ------------------------------------------------------- |
| newsletter     | _(none — body alone is enough)_                         |
| hacker-news    | `hn-points`, `hn-comments`, `hn-id`                     |
| financial-times | `ft-section`, `ft-byline`, `ft-article-url`            |
| x-twitter      | `x-account`, `x-tweet-id`, `x-reply-to`                 |

`extra` keys must not collide with the canonical keys above — `NewsItem`
raises at construction time if they do.

## Path layout

```
<vault_root>/00-Inbox/news/YYYY-MM-DD/<slug>.md
```

`YYYY-MM-DD` is `received_at.astimezone(UTC).date()`. Slug is
`slugify_subject(subject, message_id=...)`: ASCII-folded, lowercased,
non-alphanumerics collapsed to hyphens, truncated to 60 chars, with a 6-char
`sha256(message_id)` suffix to avoid collisions across same-subject items.

## Plugging in a new source

1. **Add a `source-type` literal.** Edit `news_pipeline/item.py` —
   `SourceType` is a closed `Literal`. Adding a value is one line.
2. **Build the source-specific adapter.** Sit it next to
   `news_email_adapter.py` in `daemon/src/audio_ingest/`. It owns:
   transport (HTTP poll, RSS, IMAP, websocket); body rendering to markdown;
   and constructing `NewsItem` from whatever the source returns.
3. **Build a state DB that satisfies `NewsSourceState`.** Per-source dedupe
   key shapes are genuinely different (HN `item_id`, FT `article_url`, X
   `tweet_id`) — keep the per-source SQL schema, but the listener-facing
   surface must match the protocol exactly. Reuse the SQLite file
   (`email_ingest_state_db_path`); add a new table per source.
4. **Wire it into the orchestrator.** Behind a feature flag
   (`<SOURCE>_INGEST_ENABLED`). The existing news listener pattern in
   `audio_ingest.orchestrator` is the template.
5. **Exercise the schema.** Run the daemon's
   `tests/test_news_frontmatter_golden.py`. If your new source needs its own
   golden file, add a fixture and a parameter; do not loosen the existing
   golden assertions.

## State protocol

```python
class NewsSourceState(Protocol):
    async def get_uidnext_checkpoint(self) -> int: ...
    async def set_uidnext_checkpoint(self, uidnext: int) -> None: ...
    async def is_processed(self, key: str) -> bool: ...
    async def record_processed(self, key: str, vault_path: str) -> None: ...
```

`record_processed` is the one-shot path for sources that don't need an
intermediate "received but not yet written" state (HN/FT/X are pull-based).
The newsletter adapter still uses the explicit two-step
`insert_event` + `update_status('written', ...)` pattern because IMAP IDLE
push gives it a real intermediate window where a crash could otherwise leave
the row stuck. Both DBs (`EmailIngestStateDB`, `NewsIngestStateDB`)
structurally satisfy this protocol.

## Regenerating the golden frontmatter snapshots

If you change the schema deliberately, regenerate the goldens and update every
downstream consumer in the same PR:

```bash
cd daemon
.venv/bin/python - <<'EOF'
import tempfile
from pathlib import Path
from email_ingest import parse_email
from news_pipeline import write_news_item
from audio_ingest.news_email_adapter import email_to_news_item, render_body

F = Path("tests/fixtures/news")
for name in ("html_newsletter.eml", "plaintext_newsletter.eml"):
    parsed = parse_email((F / name).read_bytes())
    body_md = render_body(parsed)
    item = email_to_news_item(parsed, body_md)
    with tempfile.TemporaryDirectory() as td:
        path = write_news_item(item, Path(td))
        content = path.read_text()
    end = content.index("\n---\n", 4) + len("\n---\n")
    (F / "golden" / name.replace(".eml", ".frontmatter")).write_text(
        content[:end], encoding="utf-8"
    )
EOF
```

Then audit the master-doc generator (`audio_ingest.news_daily_master`) and any
in-vault notes that reference the changed keys.
