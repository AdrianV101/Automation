// pm2 process definition for the automation daemon.
//
// Replaces the macOS LaunchAgent com.adrian.automation-daemon.plist. It runs
// the SAME launcher (daemon/scripts/run-daemon.sh), which rebuilds PATH (for
// the `claude` CLI + nvm node), derives the timezone, and then execs
// `.venv/bin/python -m automation_daemon`. Running the launcher rather than
// python directly preserves that environment setup exactly.
//
// Secrets are NOT defined here: run-daemon.sh execs the daemon with cwd at
// daemon/, and config.py loads daemon/.env (gitignored) itself via
// load_dotenv(). See daemon/.env.example for the required keys.
//
// Boot persistence comes from the already-registered `pm2 startup launchd`
// agent plus `pm2 save`.
const path = require("path");

const DAEMON_DIR = path.join(__dirname, "daemon");
const LOG_DIR = path.join(
  process.env.HOME,
  "Library",
  "Logs",
  "automation-daemon",
);

module.exports = {
  apps: [
    {
      name: "automation-daemon",
      script: path.join(DAEMON_DIR, "scripts", "run-daemon.sh"),
      interpreter: "/bin/sh",
      cwd: DAEMON_DIR,

      // Always-on daemon. Restart on crash, but — like the plist's
      // KeepAlive{SuccessfulExit: false} — stay stopped on a clean exit
      // (graceful SIGTERM shutdown returns 0).
      autorestart: true,
      stop_exit_codes: [0],

      // Mirror the plist's ThrottleInterval=10: wait 10s before respawning.
      restart_delay: 10000,

      // Preserve the launchd log destinations (StandardOutPath /
      // StandardErrorPath). `pm2 logs automation-daemon` tails these too.
      out_file: path.join(LOG_DIR, "stdout.log"),
      error_file: path.join(LOG_DIR, "stderr.log"),
      time: false,
    },
  ],
};
