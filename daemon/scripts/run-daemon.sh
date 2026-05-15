#!/bin/sh
# Launcher for the audio-ingest daemon under macOS launchd.
#
# launchd starts jobs with a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin).
# The daemon shells out to the `claude` CLI and node (Claude Agent SDK),
# which live in ~/.local/bin and nvm respectively. Reconstruct a usable
# PATH here so agent runs don't fail with "command not found". Resolving
# the nvm bin dir at launch (not install) time means a node upgrade does
# not silently break the daemon.
set -eu

DAEMON_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DAEMON_DIR"

# Highest-versioned installed nvm node, if any.
NODE_BIN="$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1 || true)"

PATH="$HOME/.local/bin:${NODE_BIN:+$NODE_BIN:}/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH

# The daemon's local-time schedulers (news-daily, Hacker News) resolve
# their zone via the NEWS_DAILY_MASTER_TZ env var, falling back to
# ZoneInfo("localtime") — which has no backing file on macOS and so
# drops to UTC. Derive the IANA zone from the system symlink and export
# it as NEWS_DAILY_MASTER_TZ (the variable the code actually reads).
# Only set it if unset so an explicit value in .env still wins. Also
# export TZ for any naive-local datetime use elsewhere. Derived at
# launch so it tracks System Settings > Date & Time without a reinstall.
_tz_link="$(readlink /etc/localtime 2>/dev/null || true)"
case "$_tz_link" in
    */zoneinfo/*)
        _zone="${_tz_link##*/zoneinfo/}"
        : "${NEWS_DAILY_MASTER_TZ:=$_zone}"
        export NEWS_DAILY_MASTER_TZ
        : "${TZ:=$_zone}"
        export TZ
        ;;
esac

exec "$DAEMON_DIR/.venv/bin/python" -m automation_daemon
