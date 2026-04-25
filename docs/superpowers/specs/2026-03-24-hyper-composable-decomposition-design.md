# Hyper-Composable Decomposition Design

## Overview

Decompose the monolithic `audio_ingest` flat package (~20 source files, all concerns in one namespace) into 4 independent libraries + 1 thin orchestrator daemon. Primary drivers: developer ergonomics, replaceability, and isolated testing.

## Target Architecture

### Repo Structure

```
Automation/
├── libs/
│   ├── plaud-api/          # Reverse-engineered Plaud REST + WebSocket client
│   │   ├── src/plaud_api/
│   │   ├── tests/
│   │   └── pyproject.toml
│   ├── agent-infra/         # Claude Agent SDK plumbing (streaming, sessions, MCP)
│   │   ├── src/agent_infra/
│   │   ├── tests/
│   │   └── pyproject.toml
│   ├── telegram-interface/  # Telegram bot + command dispatch + trace streaming
│   │   ├── src/telegram_interface/
│   │   ├── tests/
│   │   └── pyproject.toml
│   └── pkm/                 # PKM writing patterns (beyond Obsidian MCP)
│       ├── src/pkm/
│       ├── tests/
│       └── pyproject.toml
├── daemon/                  # Thin orchestrator — wires libraries together
│   ├── src/audio_ingest/
│   ├── tests/
│   └── pyproject.toml       # Path deps on all 4 libs
└── pyproject.toml           # Optional workspace root
```

### Dependency Graph

```
                    ┌─────────────┐
                    │   daemon    │
                    └──────┬──────┘
          ┌────────┬───────┼────────┬────────┐
          ▼        ▼       ▼        ▼        ▼
    ┌──────────┐ ┌──────┐ ┌──────────────┐ ┌─────┐
    │ plaud-api│ │ pkm  │ │telegram-iface│ │agent│
    │          │ │      │ │      │       │ │infra│
    └──────────┘ └──────┘ └──────┼───────┘ └─────┘
                                 │            ▲
                                 └────────────┘
```

- **No cycles.** One inter-library dep: telegram-interface → agent-infra.
- **plaud-api, agent-infra, pkm** are fully independent.
- **daemon** is the only integration point.

## Design Decisions

### Monorepo with Independent Packages (not separate repos)
Each library has its own `pyproject.toml`, own test suite, independently installable. But all live in one repo for easy cross-cutting refactors. Daemon uses path dependencies.

### Protocols over Shared Types (no shared core package)
Libraries define their own types and protocols for what they need. No shared `automation-core` dependency. Python structural typing (protocols) provides decoupling without inheritance. The daemon maps between library types.

**`models.py` distribution:** The current shared types file is dissolved — each library defines its own version of the types it needs:
- `Transcript` / `TranscriptSegment` → duplicated into `plaud_api` (as its output type) and `pkm` (as `TranscriptData`, its input type). These are simple value objects; the daemon maps between them.
- `RecordingJob` → stays in daemon (pipeline orchestration concept).
- `StatusTracker` / `NullStatusTracker` → stays in daemon (pipeline status callback). The plaud-api library defines its own state update methods; the daemon adapts between them.

### Bottom-Up Extraction Order
Extract incrementally in dependency order, one library per PR:
1. `plaud-api` (zero internal deps)
2. `agent-infra` (zero internal deps)
3. `telegram-interface` (depends on agent-infra)
4. `pkm` (zero internal deps)
5. Slim down daemon to orchestration only

### WhisperX Deferred
WhisperX code is in git history for later extraction as its own package. The `Transcript` interface is designed so it can plug in when needed.

### Shared SQLite, Separate Tables
PlaudStateDB and ThreadStore coexist in the same SQLite file via different table names. Each library accepts a `db_path` parameter — neither knows about the other's tables.

---

## Library Specifications

### 1. `libs/plaud-api/`

**Purpose:** Standalone client for the reverse-engineered Plaud API.

**Modules:**

| Current file | Becomes |
|---|---|
| `plaud_client.py` | `plaud_api/client.py` |
| `plaud_state.py` | `plaud_api/state.py` |
| `plaud_downloader.py` | `plaud_api/downloader.py` |
| `plaud_websocket.py` | `plaud_api/websocket.py` (refactored) |
| `plaud_transcript.py` | `plaud_api/transcript.py` |
| `plaud_adapter.py` | Deleted — logic moves to daemon |

**Key refactoring — WebSocket God Function:**

The current `run_plaud_websocket_loop()` mixes Plaud, pipeline, and Telegram concerns. In the library, it becomes a callback-driven event loop:

```python
async def run_websocket_loop(
    client: PlaudClient,
    state_db: PlaudStateDB,
    on_new_recording: Callable[[DownloadedRecording], Awaitable[None]],
    on_status: Callable[[str], Awaitable[None]] | None = None,
):
```

The daemon provides callbacks that trigger pipeline processing and Telegram notifications.

The current `run_plaud_websocket_loop()` also handles startup orchestration (token validation, user info fetch, state DB init, catch-up downloads, pipeline topic management). These daemon-side concerns move to the daemon's `orchestrator.py` — the library only owns WebSocket connection, reconnection, and event dispatching. The daemon calls `PlaudClient` methods directly for token validation and user info, then passes the results to the library's WebSocket loop.

**Public API:** `PlaudClient`, `PlaudWebSocket`, `PlaudStateDB`, `PlaudRecording`, `DownloadedRecording`, `download_new_recordings`, `plaud_segments_to_transcript`

**External deps:** `httpx`, `websockets`, `aiosqlite`

---

### 2. `libs/agent-infra/`

**Purpose:** Shared Claude Agent SDK plumbing — streaming agent loops, trace events, session management.

**Modules (split from `agent_sdk.py` + `session_manager.py`):**

| Content | Becomes |
|---|---|
| `TraceEvent` | `agent_infra/events.py` |
| `AgentLoopResult` | `agent_infra/results.py` |
| `build_agent_options()` | `agent_infra/options.py` |
| `run_agent_loop()`, `run_agent_loop_streaming()` | `agent_infra/runner.py` |
| `extract_file_path()`, `parse_date()` | `agent_infra/utils.py` |
| `SessionManager` | `agent_infra/sessions.py` |
| `DISALLOWED_NATIVE_TOOLS` | `agent_infra/options.py` |
| Tool allow-lists, people strings | **Daemon** (domain-specific) |

**Session persistence protocol:**

The current `SessionManager` takes a concrete `ThreadDB` and uses Telegram-specific fields (`thread_id`, `name`, `command`) via `ThreadRecord`. This needs refactoring: `SessionManager` should only care about session persistence (opaque key → session_id), not Telegram metadata.

```python
class SessionStore(Protocol):
    async def get_session_id(self, key: str) -> str | None: ...
    async def save_session_id(self, key: str, session_id: str) -> None: ...
```

`SessionManager` uses `SessionStore` for session resume only. Telegram-specific metadata (thread name, command type) is stored by `ThreadStore` in the telegram-interface library separately — it manages its own `threads` table and wraps `SessionStore` with the extra fields. The `SessionManager.send()` method is refactored to accept an opaque `session_key: str` rather than a `thread_id: int`, pushing the key construction to the caller (TII).

**External deps:** `claude-agent-sdk`

---

### 3. `libs/telegram-interface/`

**Purpose:** Telegram bot with agent-powered commands, forum topics, and trace streaming.

**Modules:**

| Current file | Becomes |
|---|---|
| `telegram.py` | `telegram_interface/bot.py` |
| `telegram_commands.py` | `telegram_interface/commands.py` (refactored) |
| `trace_renderer.py` | `telegram_interface/trace.py` |
| `thread_db.py` | `telegram_interface/thread_store.py` |

**Key refactoring — decouple from domain knowledge:**

`PlaudStateDB` dependency in `/status` replaced by a protocol:

```python
class StatusProvider(Protocol):
    async def get_status_sections(self) -> list[StatusSection]: ...

@dataclass
class StatusSection:
    title: str
    entries: list[str]
```

Hardcoded system prompts and tool lists replaced by configurable commands:

```python
@dataclass
class CommandConfig:
    name: str
    system_prompt: str
    tools: list[str]
    description: str

class TelegramInterface:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        commands: list[CommandConfig],
        session_manager: SessionManager,
        status_provider: StatusProvider | None = None,
    ): ...
```

The daemon registers commands with domain-specific prompts. TII handles dispatch, topics, streaming, formatting.

`ThreadStore` implements `SessionStore` protocol from agent-infra, providing Telegram-specific persistence (thread_id → session_id mapping).

**`TelegramConfig` ownership:** The library defines its own config type internally (bot_token + chat_id). The public interface accepts primitives (as shown in `TelegramInterface.__init__`). Internal functions that currently accept `TelegramConfig` are refactored to accept the library's own config type or primitives. `chat_id` is `str` (matching the current codebase — Telegram API accepts string chat IDs).

**`user_context.py` injection:** The current `telegram_commands.py` imports `USER_NAME` and `KNOWN_PEOPLE_ONELINER` directly. After extraction, these are injected via `CommandConfig.system_prompt` (the daemon interpolates user context into prompts before passing them to TII) and via constructor params where needed. No try/except import pattern in the library.

**Daemon imports from TII:** The daemon's `extraction.py` (formerly `agent_router.py`) imports `TelegramStreamSender` from telegram-interface for streaming agent traces during audio extraction. This is expected — the daemon depends on all 4 libraries.

**Depends on:** `agent-infra` (TraceEvent, SessionManager, SessionStore protocol)

**External deps:** `httpx`, `aiosqlite`

---

### 4. `libs/pkm/`

**Purpose:** PKM writing patterns beyond what Obsidian MCP provides. Extensible pattern for "input source → vault".

**Modules:**

| Current file | Becomes |
|---|---|
| `pkm_writer.py` | `pkm/writers/transcript.py` |

**Architecture:**

```python
class PKMWriter(Protocol):
    async def write(self, vault_path: str, content: Any) -> str: ...

@dataclass
class TranscriptData:
    job_id: str
    recorded_at: str
    duration_seconds: float
    speakers: list[str]
    segments: list[TranscriptSegment]
    full_text: str

class TranscriptWriter:
    def __init__(self, vault_path: str, source: str = "unknown"): ...
    async def write(self, transcript: TranscriptData) -> str: ...
```

Owns its own `parse_date` utility (trivial, no external dep). Future writers (CalendarEventWriter, EmailSummaryWriter) follow the same pattern.

**External deps:** None (stdlib only)

---

### 5. `daemon/` (Slimmed Orchestrator)

**What remains (~8 files, down from ~20):**

| File | Role |
|---|---|
| `__main__.py` | CLI entry point |
| `cli.py` | Subcommand registration |
| `config.py` | Env loading, constructs library-specific configs |
| `orchestrator.py` | Wires libraries, runs concurrent tasks |
| `pipeline.py` | Composes library calls into audio processing flow |
| `user_context.py` | People, project routes, system prompts (gitignored) |
| `models.py` | `RecordingJob`, `StatusTracker` — daemon-owned pipeline types |
| `plaud_adapter.py` | Maps plaud-api types → pipeline/PKM types |
| `extraction.py` | Audio-specific extraction agent (renamed from `agent_router.py`) |

**Config god object replaced** by constructing library-specific configs at startup:

```python
async def run_daemon():
    env = load_env()
    plaud_client = PlaudClient(token=env.plaud_token, ...)
    plaud_state = PlaudStateDB(env.state_db_path)
    thread_store = ThreadStore(env.state_db_path)  # same SQLite file
    session_mgr = SessionManager(agent_opts, session_store=thread_store)
    tii = TelegramInterface(
        bot_token=env.telegram_bot_token,
        commands=build_commands(env),
        session_manager=session_mgr,
        status_provider=PlaudStatusProvider(plaud_state),
    )
    transcript_writer = TranscriptWriter(vault_path=env.vault_path, source="plaud")
    # ...
```

**`agent_router.py` stays in daemon** as `extraction.py` — domain-specific to audio transcripts, using agent-infra's `run_agent_loop_streaming()`. Imports `TelegramStreamSender` from telegram-interface for trace streaming.

**`orchestrator.py` absorbs WebSocket orchestration:** The startup logic currently in `run_plaud_websocket_loop()` (token validation, user info fetch, pipeline topic management, catch-up downloads, stuck-recording processing) moves here. The orchestrator calls plaud-api's library functions directly for these, then starts the library's WebSocket event loop with callbacks.

---

## Testing Strategy

Each library runs its own test suite independently:

```bash
cd libs/plaud-api && python -m pytest tests/ -v
cd libs/agent-infra && python -m pytest tests/ -v
cd libs/telegram-interface && python -m pytest tests/ -v
cd libs/pkm && python -m pytest tests/ -v
cd daemon && python -m pytest tests/ -v   # integration/composition tests
```

Existing tests (~350) get redistributed to the library that owns the code being tested. Daemon tests cover the mapping/wiring layer.

**`parse_date` duplication:** Both `agent-infra` (from `agent_sdk.py`) and `pkm` (from `pkm_writer.py`) currently use `parse_date`. After extraction, each library owns its own copy — it's a ~5-line ISO 8601 parser, trivial to duplicate. The import from `agent_sdk` in `pkm_writer.py` is explicitly severed.

---

## Future Extensibility

**New input source (e.g., Google Calendar):**

1. Create `libs/google-calendar/` with its own API client
2. Add `pkm/writers/calendar.py` for vault formatting
3. Add adapter in daemon mapping calendar types → PKM types
4. Register new TII commands if needed (just add `CommandConfig`)
5. Add new `StatusProvider` section for `/status` dashboard

No existing library code changes required — only daemon wiring.
