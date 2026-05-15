#!/bin/sh
# Install (or reinstall) the automation daemon as a macOS LaunchAgent.
# Runs as the logged-in user, starts at login, restarts on crash only.
# Safe to re-run: it boots out any existing instance first.
set -eu

DAEMON_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.adrian.automation-daemon"
# Pre-rename label. The daemon does far more than audio ingest now; the
# old agent is decommissioned on install so we never end up with two
# pollers on one Telegram bot token (causes 409 Conflict loops).
LEGACY_LABEL="com.adrian.audio-ingest"
LOG_DIR="$HOME/Library/Logs/automation-daemon"
PLIST_SRC="$DAEMON_DIR/$LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
LEGACY_PLIST="$HOME/Library/LaunchAgents/$LEGACY_LABEL.plist"
DOMAIN="gui/$(id -u)"

[ -f "$PLIST_SRC" ] || { echo "template not found: $PLIST_SRC" >&2; exit 1; }
[ -x "$DAEMON_DIR/.venv/bin/python" ] || {
    echo "venv python missing at $DAEMON_DIR/.venv/bin/python — run 'uv sync' first" >&2
    exit 1
}
chmod +x "$DAEMON_DIR/scripts/run-daemon.sh"

mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"

sed -e "s#@@DAEMON_DIR@@#$DAEMON_DIR#g" \
    -e "s#@@LOG_DIR@@#$LOG_DIR#g" \
    "$PLIST_SRC" > "$PLIST_DST"

# Reinstall cleanly. Bootout the current label AND the legacy one,
# remove the legacy plist so it cannot relaunch, then reap any untracked
# stragglers (a previous bad install, or a manually started copy) so the
# new instance is the only Telegram poller. bootstrap + RunAtLoad in the
# plist starts exactly one instance; do NOT also kickstart here, that
# races KeepAlive and spawns an orphan.
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootout "$DOMAIN/$LEGACY_LABEL" 2>/dev/null || true
rm -f "$LEGACY_PLIST"
# Reap stragglers from both the pre-rename module (audio_ingest, still
# running on hosts not yet migrated) and the current one. The legacy
# arm can be dropped once every host has been reinstalled post-rename.
pkill -f "$DAEMON_DIR/.venv/bin/python -m audio_ingest" 2>/dev/null || true
pkill -f "$DAEMON_DIR/.venv/bin/python -m automation_daemon" 2>/dev/null || true
sleep 2
launchctl bootstrap "$DOMAIN" "$PLIST_DST"

echo "Installed and started $LABEL"
echo "  plist:   $PLIST_DST"
echo "  logs:    $LOG_DIR/{stdout,stderr}.log"
echo "  status:  launchctl print $DOMAIN/$LABEL | grep -E 'state|pid'"
echo "  stop:    launchctl bootout $DOMAIN/$LABEL"
echo "  restart: launchctl kickstart -k $DOMAIN/$LABEL"
