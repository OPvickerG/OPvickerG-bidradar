#!/usr/bin/env bash
# Started by launchd (see install_helper.sh). Reads the database address from Keychain and runs the scan worker
# in "helper" role: scans first while the Mac is awake, never sends email (the server does that).
set -euo pipefail
cd "$(dirname "$0")/../.."
DATABASE_URL="$(security find-generic-password -a bidradar -s bidradar-database-url -w)" || {
  echo "$(date) no database address in Keychain; run scripts/mac/install_helper.sh"; sleep 300; exit 1; }
export DATABASE_URL
export PYTHONPATH=. APP_ENV=production SCAN_ROLE=helper SEND_EMAIL=false WORKER_NAME=mac-helper \
       PUBLIC_BASE_URL=https://bidradar.onvoroa.com EMAIL_PROVIDER=console \
       SCAN_LEASE_MINUTES=5 SCANNER_MAX_PAGES_PER_TERM=4 SCANNER_SWEEP_PAGES=20 SCANNER_REQUEST_DELAY=3
exec .venv/bin/python -m scanner.scheduler.worker
