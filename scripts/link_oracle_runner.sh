#!/usr/bin/env bash
# Registers the Oracle Mumbai VM as this repository's GitHub Actions runner (label "india").
# Usage: scripts/link_oracle_runner.sh <VM_PUBLIC_IP>
# You will be asked for the one-hour registration token shown at
#   https://github.com/OPvickerG/OPvickerG-bidradar/settings/actions/runners/new  (the value after --token)
# The token is read without echo and sent to the VM over SSH stdin; it is never written to disk here.
set -euo pipefail
IP="${1:?usage: $0 <VM_PUBLIC_IP>}"
KEY="$HOME/.ssh/bidradar_oracle"
REPO_URL="https://github.com/OPvickerG/OPvickerG-bidradar"

read -r -s -p "Paste the GitHub runner token, then press Enter: " TOKEN; echo
[ -n "$TOKEN" ] || { echo "No token entered."; exit 1; }

printf '%s' "$TOKEN" | ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "ubuntu@$IP" "
  set -e
  T=\$(cat)
  cd /home/runner/actions-runner
  sudo -u runner ./config.sh --unattended --url '$REPO_URL' --token \"\$T\" --name oracle-mumbai --labels india --replace
  sudo ./svc.sh install runner
  sudo ./svc.sh start
  sudo ./svc.sh status | head -6
"
echo "Runner linked. It should now show as 'Idle' at $REPO_URL/settings/actions/runners"
