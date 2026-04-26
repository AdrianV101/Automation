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
1. **Audio ingestion pipeline**: Plaud Note Pro -> Plaud cloud -> daemon (WebSocket + direct API) -> local transcription (WhisperX) -> speaker diarization -> information extraction -> auto-sort into PKM
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

The system is decomposed into **3 independent libraries + 1 thin orchestrator daemon**. Each library is independently installable, testable, and versionable. The daemon is the only integration point.

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
│   └── pkm/                    → import pkm (standalone, stdlib only)
│       ├── src/pkm/            # PKM writing patterns (beyond Obsidian MCP)
│       ├── tests/
│       └── pyproject.toml
├── daemon/                     → import audio_ingest (depends on the libs)
│   ├── src/audio_ingest/       # Thin orchestrator — wires libraries together
│   ├── tests/
│   └── pyproject.toml          # Path deps on libs
└── docs/
```

### Dependency Graph

```
                    ┌──────────┐
                    │  daemon  │
                    └────┬─────┘
              ┌─────────┬┴────────┐
              ▼         ▼         ▼
            ┌─────┐ ┌──────────────┐ ┌──────┐
            │ pkm │ │telegram-iface│ │agent │
            │     │ │      │       │ │infra │
            └─────┘ └──────┼───────┘ └──────┘
                           │            ▲
                           └────────────┘
```

- **No cycles.** One inter-library dep: telegram-interface -> agent-infra.
- **agent-infra, pkm** are fully independent.
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

### Daemon Modules

The daemon (`audio_ingest`) is a thin orchestrator wiring the libraries together:

| File | Role |
|---|---|
| `__main__.py` | CLI entry point |
| `cli.py` | Subcommand registration |
| `config.py` | Flat `DaemonConfig` from env, constructs library-specific configs |
| `orchestrator.py` | Wires libraries, runs concurrent tasks (WebSocket, Telegram poller) |
| `pipeline.py` | Composes library calls into audio processing flow |
| `extraction.py` | Audio-specific extraction agent (uses agent-infra streaming + TII trace sender) |
| `models.py` | `RecordingJob`, `StatusTracker` -- daemon-owned pipeline types |
| `notifications.py` | Telegram notification helpers |
| `status_provider.py` | Implements TII's `StatusProvider` protocol for `/status` command |
| `user_context.py` | People, project routes, system prompts (gitignored -- use `user_context.example.py`) |

### Tech Stack (Current/Referenced)
- Obsidian + Obsidian Sync, Claude Code + Opus 4.6, Claude Agent SDK, MCP, Plaud Note Pro, WhisperX, Google Calendar, NVIDIA GPU (local inference)

### Cautionary Reference
- **Clawdbot** (C-L-A-W-D-B-O-T) - autonomous AI assistant that bought a $3k scam course + $1k domain without user consent. Example of what happens without proper guardrails and human-in-the-loop.

## Tools & Integrations
- **Obsidian PKM**: Access via `mcp__obsidian-pkm__vault_*` MCP tools (read, write, search, list)

## Deployed Services

### Audio Ingestion Pipeline
- **Python daemon**: systemd service `audio-ingest` -- manage with `systemctl {status|restart|stop} audio-ingest`, logs via `journalctl -u audio-ingest -f`
- **Config**: `daemon/.env` (secrets, not committed) -- created from `daemon/.env.example`
- **Telegram notifications**: bot token + chat ID configured via env vars
- **Data flow**: Plaud cloud -> daemon (WebSocket real-time) -> download .ogg -> convert WAV -> WhisperX transcription (subprocess) -> speaker tracking/clustering -> raw transcript to PKM -> Agent SDK extraction + routing (Opus 4.6 + Obsidian MCP) -> Telegram
- **Speaker tracking**: Unknown speakers auto-clustered, Telegram labeling prompts for naming, .npy profile creation. Concurrent Telegram long-poller runs alongside WebSocket.

## Development Notes

### Environment Setup
- Each library and the daemon have their own venv (managed by `uv`)
- Daemon depends on the libraries via editable path deps in `pyproject.toml`
- Install deps: `uv sync` or `uv pip install <package>` (run from the relevant package directory)
- `sudo` not available from Claude Code -- systemd operations require manual execution

### Running Tests

Each package runs its own test suite independently:

```bash
# Libraries (use each library's own venv)
cd libs/agent-infra && .venv/bin/python -m pytest tests/ -v
cd libs/telegram-interface && .venv/bin/python -m pytest tests/ -v
cd libs/pkm && .venv/bin/python -m pytest tests/ -v

# Daemon (integration/composition tests)
cd daemon && .venv/bin/python -m pytest tests/ -v
```

System python lacks packages -- always use `.venv/bin/python`.

### Gotchas
- Plaud API: `/user/me` returns nested structure -- `id_hash` is inside `data_user`, not top-level
- After code changes to the daemon, must `sudo systemctl restart audio-ingest` to pick up changes
- Speaker embeddings are 192-dim float32 vectors, stored as BLOB (768 bytes) in `speaker_clusters` SQLite table
- Speaker tracking in pipeline is non-fatal (try/except) -- transcription proceeds even if clustering fails
- PKM vault path: set via `PKM_VAULT_PATH` env var
- WhisperX 3.7+ moved `DiarizationPipeline` to `whisperx.diarize.DiarizationPipeline` (not top-level)
- PyTorch 2.8+ defaults `torch.load(weights_only=True)` -- worker patches it at module level before any whisperx import
- WhisperX/pyannote leak logs and warnings to stdout -- worker redirects `sys.stdout` to `sys.stderr` during `run()`, writes JSON to `real_stdout` after
- Extraction uses Claude Agent SDK (Opus 4.6) with Obsidian MCP -- agent autonomously routes to appropriate PKM locations
- Extraction agent: `daemon/src/audio_ingest/extraction.py` -- spawns Claude agent with `bypassPermissions`, max 15 turns, all `mcp__obsidian-pkm__vault_*` tools
- On agent failure, raw transcript is preserved but no extraction/routing occurs
- Audio files (OGG + WAV) are retained locally in `daemon/audio_downloads/` -- no automatic cleanup
- PKM output: raw transcript -> `04-Archive/transcripts/`, agent routes summaries/tasks/notes to appropriate locations
- Test the worker directly: `echo '<json config>' | .venv/bin/python -m audio_ingest.whisperx_worker` -- much faster iteration than restarting systemd
- Config is a flat `DaemonConfig` dataclass (not nested sub-configs) -- libraries receive only the fields they need via constructor args
- Each library has its own venv; daemon depends on libraries via path deps in `pyproject.toml`
- Shared SQLite, separate tables: `PlaudStateDB` and `ThreadStore` coexist in the same SQLite file via different table names, each accepting a `db_path` parameter
- `user_context.py` is gitignored -- copy `user_context.example.py` and fill in your details
