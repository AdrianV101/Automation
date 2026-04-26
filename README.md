# Automation

An AI-augmented personal knowledge management (PKM) system that captures voice recordings, extracts information using Claude, and routes it into an Obsidian vault -- autonomously, with human approval for anything external.

## What it does

1. **Audio ingestion**: Monitors a [Plaud Note Pro](https://www.plaud.ai/) account via WebSocket for new recordings
2. **Transcription**: Downloads audio and transcribes locally using [WhisperX](https://github.com/m-bain/whisperX) with speaker diarization
3. **AI extraction**: A Claude agent (via [Agent SDK](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/agent-sdk)) reads the transcript, searches the Obsidian vault for context, and routes extracted information to the right locations
4. **Notifications**: Sends progress updates and results via Telegram

```
Plaud Cloud --> WebSocket --> Download .ogg
                                  |
                            WhisperX (local GPU)
                                  |
                            Raw transcript --> PKM archive
                                  |
                            Claude Agent (Opus) + Obsidian MCP
                                  |
                     +------------+------------+
                     |            |            |
                  Summaries    Tasks     Project notes
                  (00-Inbox)   (project   (append to
                               tasks/)    existing)
```

## Architecture

A **monorepo of composable libraries** -- four independent packages with clear interfaces, composed by a thin daemon orchestrator. Each library owns one domain, has its own venv, and can be developed and tested in isolation.

```
Automation/
├── libs/
│   ├── agent-infra/          # Claude Agent SDK runner, sessions, MCP config (66 tests)
│   ├── telegram-interface/   # Telegram bot, command dispatch, trace streaming (178 tests)
│   └── pkm/                  # PKM writing patterns (3 tests)
├── daemon/                   # Thin orchestrator wiring libs into a pipeline (91 tests)
└── docs/                     # Design specs and architecture docs
```

### Libraries

**`libs/agent-infra/`** -- Claude Agent SDK infrastructure
- `run_agent_loop` -- executes a Claude agent with tool access and turn limits
- `build_agent_options` -- constructs agent configuration from typed inputs
- `SessionManager` -- multi-turn conversation sessions (`SessionStore` protocol for pluggable backends)
- `TraceEvent` -- structured event stream from agent execution

**`libs/telegram-interface/`** -- Telegram bot and UI
- `TelegramInterface` -- bot lifecycle with `CommandConfig` pattern for declarative command registration
- `ThreadStore` -- persistent conversation thread storage
- `TelegramStreamSender` -- real-time agent trace streaming to Telegram chats
- Bot API client -- typed wrapper over Telegram HTTP API

**`libs/pkm/`** -- PKM writing patterns
- `TranscriptData` -- typed representation of transcription output
- `write_raw_transcript` -- formats and writes raw transcripts to the vault archive

### Daemon

**`daemon/`** -- Thin orchestrator that wires libraries into the audio ingestion pipeline
- `orchestrator.py` -- top-level daemon lifecycle (WebSocket loop + Telegram poller)
- `pipeline.py` -- composes recording processing steps (download, transcribe, extract, route)
- `extraction.py` -- Claude agent extraction and PKM routing
- `config.py` -- typed configuration from environment variables
- `notifications.py` -- Telegram notification helpers

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (package manager)
- A [Plaud](https://www.plaud.ai/) account with API access
- [Obsidian](https://obsidian.md/) vault + [Obsidian MCP server](https://github.com/AdrianV101/Obsidian-MCP)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code) (Agent SDK uses your Claude subscription)
- NVIDIA GPU recommended for local WhisperX transcription
- Telegram bot (via [@BotFather](https://t.me/botfather)) for notifications

## Setup

```bash
# Clone
git clone https://github.com/AdrianV101/Automation.git
cd Automation

# Install all packages (each has its own venv)
for pkg in libs/agent-infra libs/telegram-interface libs/pkm daemon; do
  cd $pkg && uv venv && uv pip install -e ".[dev]" && cd -
done

# Configure environment
cp daemon/.env.example daemon/.env
# Edit daemon/.env with your tokens, paths, and settings

# Configure user context (personal info for agent prompts)
cp daemon/src/audio_ingest/user_context.example.py daemon/src/audio_ingest/user_context.py
# Edit user_context.py with your name, known people, and project paths

# Configure CLAUDE.md (project instructions for Claude Code)
cp CLAUDE.example.md CLAUDE.md
# Edit CLAUDE.md with your deployment-specific details

# Run as systemd service
sudo cp daemon/audio-ingest.service /etc/systemd/system/
sudo systemctl enable --now audio-ingest

# Or run directly
daemon/.venv/bin/python -m audio_ingest
```

## Running tests

Each package has its own test suite and venv. 416 tests total.

```bash
# Individual packages
libs/agent-infra/.venv/bin/python -m pytest libs/agent-infra/tests/ -v      # 66 tests
libs/telegram-interface/.venv/bin/python -m pytest libs/telegram-interface/tests/ -v  # 178 tests
libs/pkm/.venv/bin/python -m pytest libs/pkm/tests/ -v                      # 3 tests
daemon/.venv/bin/python -m pytest daemon/tests/ -v                           # 91 tests
```

## Telegram commands

Once running, interact via Telegram:

| Command | Description |
|---------|-------------|
| `/note <text>` | Capture a fleeting note into the vault |
| `/ask <question>` | Query the PKM, codebase, and web |
| `/task <description>` | Log a task to the appropriate project |
| `/chat <message>` | Free-form conversation with vault context |
| `/status` | Pipeline status dashboard |

## Project status

**Work in progress.** The audio ingestion pipeline is functional. Planned capabilities:

- [ ] Calendar integration (Google Calendar)
- [ ] Inbound message triage (email, LinkedIn)
- [ ] Autonomous task execution
- [ ] Proactive prompting
- [ ] Mobile texting interface

## License

[MIT](LICENSE)
