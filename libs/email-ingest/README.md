# email-ingest

Source-agnostic primitives for email-driven ingestion pipelines.

## What's here

- **`bridge`** — low-level async IMAP client (connect, FETCH, IDLE, mark seen)
- **`listener`** — `ImapIdleListener` high-level orchestrator (reconnect, UIDNEXT resync)
- **`webhook`** — `WebhookForwarder` POSTs a structured JSON record (`sender`, `subject`, `body`, `received_at`, `message_id`) to an ingest endpoint (e.g. Poke) on each new message; supports self-authenticating URLs or an optional Bearer key; retries with backoff, never raises
- **`mime`** — `parse_email(raw_bytes) -> ParsedEmail`
- **`auth`** — `verify_dkim(msg, required_domain)` via `Authentication-Results`
- **`state`** — aiosqlite-backed `EmailIngestStateDB` + `EmailIngestStatusTracker`

## What's not here

- No source-specific parsing (e.g. Plaud attachments). Those live in the consumer (e.g. `daemon/automation_daemon/plaud_email_adapter.py`).
- No DKIM re-verification from raw RSA — we trust Proton's `Authentication-Results`.

## Usage

```python
from email_ingest import ImapIdleListener, BridgeConfig, EmailIngestStateDB

cfg = BridgeConfig(host="127.0.0.1", port=1143, user=..., password=...)
db = EmailIngestStateDB("./email_ingest_state.db")
await db.init_db()

async def on_new_email(uid, raw_bytes, parsed_headers):
    ...

listener = ImapIdleListener(cfg, db, on_new_email=on_new_email)
await listener.run()
```
