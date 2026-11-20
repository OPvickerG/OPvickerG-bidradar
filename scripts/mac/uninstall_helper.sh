#!/usr/bin/env bash
# Remove the Mac fast scanner. The Mumbai server keeps scanning and emailing on its own.
LABEL="com.opvickerg.bidradar.helper"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
security delete-generic-password -a bidradar -s bidradar-database-url >/dev/null 2>&1 || true
echo "Fast scanner removed (database address deleted from Keychain)."
