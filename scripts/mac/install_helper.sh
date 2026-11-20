#!/usr/bin/env bash
# Install the BidRadar "fast scanner" on this Mac.
#
# While the Mac is awake it scans every :00/:30 IST slot first; the Mumbai server sees its heartbeat and stands by.
# When the Mac sleeps or is offline, the server scans on time by itself. Only the server sends email.
#
# The database address is stored in your login Keychain (never in a file or in the code).
# Run:  bash scripts/mac/install_helper.sh        Remove:  bash scripts/mac/uninstall_helper.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
LABEL="com.opvickerg.bidradar.helper"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/bidradar-helper.log"

if [ ! -x .venv/bin/python ]; then
  echo "Setting up Python packages (one time)..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

if security find-generic-password -a bidradar -s bidradar-database-url >/dev/null 2>&1 && [ "${1:-}" != "--new-url" ]; then
  echo "Database address already saved in Keychain (use --new-url to replace it)."
else
  echo "Paste the Neon DATABASE_URL (the same one you pasted into Voroa), then press Enter:"
  IFS= read -r URL
  URL="$(printf '%s' "$URL" | tr -d '[:space:]')"
  echo "Got it (${#URL} characters)."
  case "$URL" in postgres://*|postgresql://*) ;; *) echo "That does not look like a postgres:// address. Nothing saved."; exit 1 ;; esac
  security add-generic-password -U -a bidradar -s bidradar-database-url -w "$URL"
  unset URL
  echo "Saved in Keychain."
fi

chmod +x scripts/mac/run_helper.sh
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$ROOT/scripts/mac/run_helper.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Fast scanner installed and running. It starts by itself at login and restarts if it ever stops."
echo "Log: $LOG"
