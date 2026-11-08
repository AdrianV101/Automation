# Automation

An AI-augmented personal knowledge management (PKM) system that captures voice recordings, extracts information using Claude, and routes it into an Obsidian vault -- autonomously, with human approval for anything external.

## What it does

1. **Audio ingestion**: A [Plaud Note Pro](https://www.plaud.ai/) recorder uploads to Plaud cloud. Every 5 minutes the daemon polls the official [Plaud MCP server](https://www.npmjs.com/package/@plaud-ai/mcp) for recordings that have a transcript and summary.
2. **Speaker-labelling trigger**: A recording is ingested once its speakers are labelled in the Plaud app: immediately when no generic `Speaker N` label is left, 10 minutes after the last label change when only some are named, or 24 hours after it first looked ready if nobody touches it. Plaud's summaries, outline and device highlights travel with the transcript.
3. **AI extraction**: A Claude agent (via [Agent SDK](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/agent-sdk)) reads the transcript, searches the Obsidian vault for context, and routes extracted information to the right locations.
4. **Problems surface in the vault**: Failed recordings and persistent Plaud MCP problems (including an expired login) are listed in `00-Inbox/plaud-ingest-attention.md`, which disappears when everything is healthy.

```
Plaud Cloud --Plaud MCP (stdio, polled every 5 min)--> daemon
                                  |
                            Ready? Speakers labelled? (trigger + SQLite state)
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
│   ├── pkm/                  # PKM writing patterns (3 tests)
│   └── email-ingest/         # IMAP IDLE + DKIM + MIME primitives (37 tests)
├── daemon/                   # Thin orchestrator wiring libs into a pipeline (95 tests)
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

**`libs/email-ingest/`** -- Source-agnostic email ingestion primitives
- `ImapBridge` -- IMAP IDLE loop with reconnect, UIDVALIDITY tracking, and last-UID checkpointing
- `parse_email` -- MIME parsing into typed `ParsedEmail` (headers, text/html bodies, attachments)
- `verify_dkim` -- DKIM signature verification + trusted-Authentication-Results acceptance
- `EmailIngestStateDB` -- SQLite checkpoint store (last UID, processed message-IDs)

### Daemon

**`daemon/`** -- Thin orchestrator that wires libraries into the audio ingestion pipeline
- `orchestrator.py` -- top-level daemon lifecycle (Plaud MCP poller, IMAP listener forwarding new mail to Poke, Telegram poller)
- `pipeline.py` -- composes processing steps (raw transcript, extract, route); `notify=False` runs it without Telegram
- `extraction.py` -- Claude agent extraction and PKM routing
- `plaud_mcp_client.py` -- stdio client for the pinned `@plaud-ai/mcp` package
- `plaud_mcp_trigger.py`, `plaud_mcp_state.py` -- speaker-labelling trigger and SQLite state per recording
- `plaud_mcp_adapter.py` -- maps MCP recordings (segments, summaries, outline, highlights) to pipeline `RecordingJob`s
- `plaud_mcp_ingest.py` -- poll cycle, retries, attention note and the manual `plaud-ingest` command
- `config.py` -- typed `DaemonConfig` from environment variables
- `notifications.py` -- Telegram notification helpers
- `prompts.py`, `tools.py`, `command_config.py` -- agent prompts, allowed tools, and Telegram command wiring

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (package manager)
- A [Plaud](https://www.plaud.ai/) account, logged in to the Plaud MCP on the daemon host (run its `login` tool once, e.g. from Claude Code; tokens live in `~/.plaud/`), plus Node.js for `npx`
- Optional: an IMAP-accessible mailbox whose new mail is forwarded to a Poke ingest webhook. The reference setup uses [Proton Mail Bridge](https://proton.me/mail/bridge) on `127.0.0.1:1143`
- [Obsidian](https://obsidian.md/) vault + [Obsidian MCP server](https://github.com/AdrianV101/Obsidian-MCP)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code) (Agent SDK uses your Claude subscription)
- Telegram bot (via [@BotFather](https://t.me/botfather)) for notifications
- *(Historical)* The earlier WhisperX local-transcription path required a CUDA GPU; it is not part of this repository's pipeline, which uses Plaud's hosted transcript

## Setup

```bash
# Clone
git clone https://github.com/AdrianV101/Automation.git
cd Automation

# Install all packages (each has its own venv)
for pkg in libs/agent-infra libs/telegram-interface libs/pkm libs/email-ingest daemon; do
  cd $pkg && uv venv && uv pip install -e ".[dev]" && cd -
done

# Configure environment
cp daemon/.env.example daemon/.env
# Edit daemon/.env with your tokens, paths, IMAP credentials, and settings

# Configure user context (personal info for agent prompts)
cp daemon/src/automation_daemon/user_context.example.py daemon/src/automation_daemon/user_context.py
# Edit user_context.py with your name, known people, and project paths

# Configure CLAUDE.md (project instructions for Claude Code)
cp CLAUDE.example.md CLAUDE.md
# Edit CLAUDE.md with your deployment-specific details

# Run directly
daemon/.venv/bin/python -m automation_daemon

# Or run as a managed service:
#   macOS: install a launchd LaunchAgent via daemon/scripts/install-launchagent.sh
#          (Label `com.adrian.automation-daemon`, RunAtLoad, KeepAlive on abnormal exit,
#          logs to ~/Library/Logs/automation-daemon/{stdout,stderr}.log)
#   Linux: sudo cp daemon/automation-daemon.service /etc/systemd/system/
#          sudo systemctl enable --now automation-daemon
```

## Plaud MCP ingest

Enable it in `daemon/.env`:

| Variable | Default | Meaning |
|---|---|---|
| `ENABLE_PLAUD_MCP_INGEST` | `false` | Start the `plaud-mcp` poller |
| `PLAUD_MCP_SWITCHOVER_DATE` | (required when enabled) | `YYYY-MM-DD`; recordings before this date are never picked up automatically |
| `PLAUD_MCP_PACKAGE` | `@plaud-ai/mcp@0.3.14` | Exact version pin; `@latest`, ranges and tags are rejected |
| `PLAUD_MCP_POLL_INTERVAL_S` | `300` | Seconds between polls |
| `PLAUD_MCP_LABEL_QUIET_S` | `600` | Wait after the last speaker-label change |
| `PLAUD_MCP_UNLABELLED_FALLBACK_S` | `86400` | Ingest with generic labels after this long untouched |
| `PLAUD_MCP_WINDOW_DAYS` | `14` | How far back each poll looks |

A recording that fails before routing is retried up to 3 times, then listed in `00-Inbox/plaud-ingest-attention.md` together with the command to re-run it. To ingest one recording by hand, including ones older than the switchover date:

```bash
daemon/.venv/bin/python -m automation_daemon plaud-ingest <file_id>            # ingest
daemon/.venv/bin/python -m automation_daemon plaud-ingest <file_id> --force    # rewrite the transcript only
daemon/.venv/bin/python -m automation_daemon plaud-ingest <file_id> --force --extract  # also re-run extraction (may duplicate notes)
```

## Running tests

Each package has its own test suite and venv. 379 tests total.

```bash
# Individual packages
libs/agent-infra/.venv/bin/python -m pytest libs/agent-infra/tests/ -v               # 66 tests
libs/telegram-interface/.venv/bin/python -m pytest libs/telegram-interface/tests/ -v # 178 tests
libs/pkm/.venv/bin/python -m pytest libs/pkm/tests/ -v                               # 3 tests
libs/email-ingest/.venv/bin/python -m pytest libs/email-ingest/tests/ -v             # 37 tests
daemon/.venv/bin/python -m pytest daemon/tests/ -v                                   # 95 tests
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
