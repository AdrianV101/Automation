# Automation - AI-Augmented PKM System

## Workspace
- This is a **planning and development workspace**, not the PKM itself
- Used to flesh out ideas, design architecture, and build tooling for the upgraded PKM
- The existing Obsidian PKM is accessible via MCP tools (see below)

## Project Vision
Building an AI-augmented PKM that centralizes all information and acts on it autonomously (with human approval).

### Current PKM State
- Obsidian vault with Obsidian Sync (phone, dev laptop, PC)
- 3 manual uses: inbox for fleeting notes, viewing project docs, occasional daily notes
- Primary usage by volume: Claude Code writes to PKM via custom Obsidian MCP (devlogs, bugs, project docs)
- Projects live under `01-Projects/{ProjectName}/` with subfolders for dev, research, etc.

### Target System Capabilities
1. **Audio ingestion pipeline**: Plaud Note Pro -> Plaud cloud -> AutoFlow email -> Proton Mail Bridge (local IMAP) -> daemon (IMAP IDLE) -> Plaud-hosted transcript (or optional local WhisperX re-transcription) -> information extraction -> auto-sort into PKM
2. **Calendar integration**: Google Calendar (multiple accounts) - meeting context, follow-ups, time-blocking
3. **Inbound message triage**: auto-triage all messages (email, LinkedIn, etc.), draft replies based on known context
4. **Autonomous task execution**: AI does research/development/admin work, asks user only for decisions it can't resolve from existing knowledge
5. **Proactive prompting**: system tells user what to focus on, what's due, what needs attention
6. **Texting interface**: interact with the system via phone like messaging a person

### Key Principles
- Never delete information - always archive
- Human-in-the-loop: never auto-send communications or make purchases
- Only ask user questions the PKM can't answer - research first
- Capture ALL information from sources, not just summaries/action items
- Maximize user's control without requiring them to direct every detail

## Architecture: Hyper-Composable Decomposition

The system is decomposed into **4 independent libraries + 1 thin orchestrator daemon**. Each library is independently installable, testable, and versionable. The daemon is the only integration point.

### Package Layout

```
Automation/
├── libs/
│   ├── agent-infra/            → import agent_infra (standalone)
│   │   ├── src/agent_infra/    # Claude Agent SDK plumbing (streaming, sessions, MCP)
│   │   ├── tests/
│   │   └── pyproject.toml
│   ├── telegram-interface/     → import telegram_interface (depends on agent-infra)
│   │   ├── src/telegram_interface/  # Telegram bot + command dispatch + trace streaming
│   │   ├── tests/
│   │   └── pyproject.toml
│   ├── pkm/                    → import pkm (standalone, stdlib only)
│   │   ├── src/pkm/            # PKM writing patterns (beyond Obsidian MCP)
│   │   ├── tests/
│   │   └── pyproject.toml
│   └── email-ingest/           → import email_ingest (standalone)
│       ├── src/email_ingest/   # IMAP IDLE + DKIM + MIME primitives
│       ├── tests/
│       └── pyproject.toml
├── daemon/                     → import automation_daemon (depends on the libs)
│   ├── src/automation_daemon/       # Thin orchestrator — wires libraries together
│   ├── tests/
│   └── pyproject.toml          # Path deps on libs
└── docs/
```

### Dependency Graph

```
                       ┌──────────┐
                       │  daemon  │
                       └────┬─────┘
        ┌──────────┬───────┼────────┐
        ▼          ▼       ▼        ▼
  ┌──────────┐ ┌─────┐ ┌──────────────┐ ┌──────┐
  │email-    │ │ pkm │ │telegram-iface│ │agent │
  │ingest    │ │     │ │      │       │ │infra │
  └──────────┘ └─────┘ └──────┼───────┘ └──────┘
                              │            ▲
                              └────────────┘
```

- **No cycles.** One inter-library dep: telegram-interface -> agent-infra.
- **agent-infra, pkm, email-ingest** are fully independent.
- **daemon** is the only integration point.

### Design Rules

1. **Each module owns one job.** A module that transcribes audio should not know about Telegram. A module that writes to PKM should not know about Plaud. If a function needs the word "and" to describe what it does, it's two modules.

2. **Interfaces are typed data, not god objects.** Modules accept only the data they need -- not a 25-field Config when they use 3 fields. Shared data flows through typed dataclasses (like `Transcript`, `AgentLoopResult`), not raw dicts or catch-all configs.

3. **Pipelines are composition, not monoliths.** An orchestrator composes primitives into a pipeline, but each step is independently callable and testable. You should be able to re-extract without re-transcribing, or feed non-Plaud audio through the same transcription + extraction path.

4. **Shared capabilities live outside source-specific packages.** Telegram, PKM writing, and agent-based extraction are general capabilities -- they serve audio today but will serve calendar, email, and other sources tomorrow. Structure the codebase so these are reachable without importing a source-specific package.

5. **New sources compose, not copy.** When adding a new input source (calendar, email, etc.), it should wire together existing primitives (extraction, PKM routing, notifications) with a thin source-specific adapter -- not reimplement the pipeline.

6. **Protocols over shared types (no shared core package).** Libraries define their own types and protocols for what they need. No shared `automation-core` dependency. Python structural typing (protocols) provides decoupling without inheritance. The daemon maps between library types.

### Library Overview

| Library | Import Name | Key Public Types | External Deps |
|---|---|---|---|
| `agent-infra` | `agent_infra` | `TraceEvent`, `AgentLoopResult`, `SessionManager`, `SessionStore` | `claude-agent-sdk` |
| `telegram-interface` | `telegram_interface` | `TelegramInterface`, `CommandConfig`, `BotConfig`, `ThreadStore` | `httpx`, `aiosqlite` |
| `pkm` | `pkm` | `TranscriptWriter`, `TranscriptData` | None (stdlib only) |
| `email-ingest` | `email_ingest` | `ImapBridge`, `parse_email`, `verify_dkim`, `EmailIngestStateDB`, `ParsedEmail`, `Attachment` | `aioimaplib`, `aiosqlite` |

### Daemon Modules

The daemon (`automation_daemon`) is a thin orchestrator wiring the libraries together:

| File | Role |
|---|---|
| `__main__.py` | CLI entry point |
| `cli.py` | Subcommand registration |
| `config.py` | Flat `DaemonConfig` from env, constructs library-specific configs |
| `orchestrator.py` | Wires libraries, runs concurrent tasks (IMAP listener, Telegram poller) |
| `pipeline.py` | Composes library calls into the email -> transcript -> extract -> route flow |
| `extraction.py` | Audio-specific extraction agent (uses agent-infra streaming + TII trace sender) |
| `plaud_email_adapter.py` | Parses Plaud-formatted emails into `RecordingJob` + PKM `TranscriptData`; handles attachments, infographics, summary link rewriting |
| `models.py` | `RecordingJob`, `StatusTracker` -- daemon-owned pipeline types |
| `notifications.py` | Telegram notification helpers |
| `prompts.py` | System prompts for the extraction agent |
| `tools.py` | Allowed-tool sets for agent-infra invocations |
| `command_config.py` | Telegram `CommandConfig` declarations (`/note`, `/ask`, `/task`, `/chat`, `/status`) |
| `user_context.py` | People, project routes, system prompts (gitignored -- use `user_context.example.py`) |

### Tech Stack (Current/Referenced)
- Obsidian + Obsidian Sync, Claude Code + Opus 4.6, Claude Agent SDK, MCP, Plaud Note Pro, Proton Mail Bridge (local IMAP), Google Calendar
- Host: Mac mini (Apple Silicon), launchd `com.adrian.automation-daemon` LaunchAgent
- *Optional path:* WhisperX local re-transcription on a CUDA GPU (off by default)

### Cautionary Reference
- **Clawdbot** (C-L-A-W-D-B-O-T) - autonomous AI assistant that bought a $3k scam course + $1k domain without user consent. Example of what happens without proper guardrails and human-in-the-loop.

## Tools & Integrations
- **Obsidian PKM**: Access via `mcp__obsidian-pkm__vault_*` MCP tools (read, write, search, list)

## Deployed Services

### Audio Ingestion Pipeline
- **Python daemon**: launchd LaunchAgent `com.adrian.automation-daemon` on the Mac mini -- plist installed by `daemon/scripts/install-launchagent.sh` at `~/Library/LaunchAgents/com.adrian.automation-daemon.plist`, manage with `launchctl {bootstrap|bootout|kickstart -k} gui/$(id -u)/com.adrian.automation-daemon` (or `launchctl print gui/$(id -u)/com.adrian.automation-daemon`), logs at `~/Library/Logs/automation-daemon/{stdout,stderr}.log`
- **Email bridge**: Proton Mail Bridge installed as a per-user LaunchAgent on the same host; exposes the Proton mailbox at `127.0.0.1:1143` (STARTTLS, self-signed cert -- `IMAP_SSL_VERIFY=false`)
- **Config**: `daemon/.env` (secrets, not committed) -- created from `daemon/.env.example`
- **Telegram notifications**: bot token + chat ID configured via env vars
- **Data flow**: Plaud cloud -> AutoFlow email to configured mailbox -> Proton Mail Bridge -> daemon IMAP IDLE -> DKIM verify + MIME parse -> save attachments to vault `99-Attachments/plaud/` -> raw transcript to PKM -> Agent SDK extraction + routing (Opus 4.6 + Obsidian MCP) -> Telegram. Concurrent Telegram long-poller runs alongside the IMAP listener.
- **State**: `daemon/email_ingest_state.db` (last UID, processed message-IDs, UIDVALIDITY) + `daemon/plaud_state.db` (Telegram thread store, pipeline status). Both SQLite, separate files post-cutover.
- **Optional WhisperX path**: gated by `WHISPERX_ENABLED=false` by default. When enabled (CUDA GPU host), the daemon re-transcribes the audio attachment locally with speaker diarization/clustering instead of using Plaud's hosted transcript.

## Development Notes

### Environment Setup
- Each library and the daemon have their own venv (managed by `uv`)
- Daemon depends on the libraries via editable path deps in `pyproject.toml`
- Install deps: `uv sync --extra dev` (run from the relevant package directory)
- Restarting the daemon after code changes: `launchctl kickstart -k gui/$(id -u)/com.adrian.automation-daemon`

### Running Tests

Each package runs its own test suite independently:

```bash
# Libraries (use each library's own venv)
cd libs/agent-infra && .venv/bin/python -m pytest tests/ -v
cd libs/telegram-interface && .venv/bin/python -m pytest tests/ -v
cd libs/pkm && .venv/bin/python -m pytest tests/ -v
cd libs/email-ingest && .venv/bin/python -m pytest tests/ -v

# Daemon (integration/composition tests)
cd daemon && .venv/bin/python -m pytest tests/ -v
```

System python lacks packages -- always use `.venv/bin/python`.

### Gotchas
- IMAP bridge: Proton Mail Bridge presents a self-signed cert on `127.0.0.1:1143` -- `IMAP_USE_STARTTLS=true` + `IMAP_SSL_VERIFY=false` are required. The Bridge runs as a per-user LaunchAgent and may briefly drop on login/logout cycles; `ImapBridge` retries with backoff
- IMAP exception logging: `ImapBridge` formats connect-failure exceptions with `%s`; some asyncio exceptions (e.g. `TimeoutError`) have empty `__str__`, so log lines may show "IMAP connect attempt N failed: " with a blank repr. Use `%r` to surface the type. Cosmetic, deferred
- DKIM: configured via `DKIM_TRUSTED_AUTHSERV_ID` (default `mail.protonmail.ch`) + `DKIM_REQUIRED_DOMAIN` (default `plaud.ai`). The daemon trusts an upstream `Authentication-Results: ...; dkim=pass` from the configured authserv-id rather than re-verifying signatures
- After code changes to the daemon, restart with `launchctl kickstart -k gui/$(id -u)/com.adrian.automation-daemon` (or `bootout` then `bootstrap`)
- PKM vault path: set via `PKM_VAULT_PATH` env var
- Email-attachment routing: Plaud emails carry the audio file + cover infographic. `plaud_email_adapter.save_plaud_attachments` writes them to `<vault>/99-Attachments/plaud/<sanitized-message-id>/...` (subdir set by `VAULT_ATTACHMENTS_SUBDIR`)
- Extraction uses Claude Agent SDK (Opus 4.6) with Obsidian MCP -- agent autonomously routes to appropriate PKM locations
- Extraction agent: `daemon/src/automation_daemon/extraction.py` -- spawns Claude agent with `bypassPermissions`, max 15 turns, all `mcp__obsidian-pkm__vault_*` tools
- On agent failure, raw transcript is preserved but no extraction/routing occurs
- PKM output: raw transcript -> `04-Archive/transcripts/`, agent routes summaries/tasks/notes to appropriate locations
- State: `email_ingest_state.db` and `plaud_state.db` are separate SQLite files in `daemon/`. The daemon's pipeline thread (`pipeline_thread_id`), Telegram `ThreadStore`, and last IMAP UID/UIDVALIDITY persist across restarts
- Config is a flat `DaemonConfig` dataclass -- libraries receive only the fields they need via constructor args
- `user_context.py` is gitignored -- copy `user_context.example.py` and fill in your details
- *Optional WhisperX path (off by default, `WHISPERX_ENABLED=false`)*: requires CUDA GPU; gotchas if enabled --
  - WhisperX 3.7+ moved `DiarizationPipeline` to `whisperx.diarize.DiarizationPipeline` (not top-level)
  - PyTorch 2.8+ defaults `torch.load(weights_only=True)` -- worker patches it at module level before any whisperx import
  - WhisperX/pyannote leak logs and warnings to stdout -- worker redirects `sys.stdout` to `sys.stderr` during `run()`, writes JSON to `real_stdout` after
  - Speaker embeddings are 192-dim float32 vectors, stored as BLOB (768 bytes) in `speaker_clusters` SQLite table
  - Speaker tracking in pipeline is non-fatal (try/except) -- transcription proceeds even if clustering fails
  - Audio files (OGG + WAV) are retained locally in `daemon/audio_downloads/` -- no automatic cleanup
  - Test the worker directly: `echo '<json config>' | .venv/bin/python -m automation_daemon.whisperx_worker` -- much faster iteration than restarting the daemon
