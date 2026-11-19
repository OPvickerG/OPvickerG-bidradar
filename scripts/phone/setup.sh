#!/data/data/com.termux/files/usr/bin/bash
# OPvickerG BidRadar — turn an Android phone (Termux) into the India-located scanner.
#
# Why: GeM only accepts connections from Indian internet addresses. GitHub Actions still schedules every scan
# (each :00 and :30 IST); this phone runs them as a GitHub self-hosted runner (label "india") over home Wi-Fi.
#
# Run inside the Termux app:
#   curl -fsSL https://raw.githubusercontent.com/OPvickerG/OPvickerG-bidradar/main/scripts/phone/setup.sh | bash
#
# It asks once for the one-hour runner token from
#   https://github.com/OPvickerG/OPvickerG-bidradar/settings/actions/runners/new   (the value after --token)
# Nothing secret is stored in this repository; the database key is delivered by GitHub to each job.
set -euo pipefail

# Everything is inside main() so bash reads the whole script before running any of it (safe with curl | bash).
main() {
  REPO_RAW="https://raw.githubusercontent.com/OPvickerG/OPvickerG-bidradar/main"

  echo "== BidRadar phone setup =="
  termux-wake-lock 2>/dev/null || true

  echo "-- Updating Termux packages (a few minutes the first time)"
  pkg update -y >/dev/null 2>&1 || true
  pkg install -y proot-distro curl termux-api >/dev/null 2>&1 || pkg install -y proot-distro curl

  if [ ! -d "$PREFIX/var/lib/proot-distro/installed-rootfs/ubuntu" ]; then
    echo "-- Installing a small Ubuntu environment (about 100 MB download)"
    proot-distro install ubuntu || true
  fi

  echo "-- Installing Python and the GitHub runner inside Ubuntu"
  proot-distro login ubuntu -- bash -c "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null && curl -fsSL '$REPO_RAW/scripts/phone/inside_ubuntu.sh' -o /root/bidradar_inside.sh && bash /root/bidradar_inside.sh"

  echo "-- Creating the start command 'bidradar-start' and boot auto-start"
  T="/data/data/com.termux/files/usr/bin/bash"
  printf '%s\n' "#!$T" \
    "# Keeps the BidRadar runner online and restarts it if it stops." \
    "termux-wake-lock 2>/dev/null || true" \
    "exec proot-distro login ubuntu -- bash -c 'cd /root/actions-runner && export RUNNER_ALLOW_RUNASROOT=1 && while true; do ./run.sh; echo runner-stopped-restarting; sleep 15; done'" \
    > "$PREFIX/bin/bidradar-start"
  chmod +x "$PREFIX/bin/bidradar-start"
  mkdir -p "$HOME/.termux/boot"
  printf '%s\n' "#!$T" \
    "termux-wake-lock 2>/dev/null || true" \
    'nohup bidradar-start > "$HOME/bidradar-runner.log" 2>&1 &' \
    > "$HOME/.termux/boot/bidradar"
  chmod +x "$HOME/.termux/boot/bidradar"

  echo "-- Starting the runner in the background"
  pkill -f "actions-runner" 2>/dev/null || true
  nohup bidradar-start > "$HOME/bidradar-runner.log" 2>&1 &
  sleep 20
  tail -n 5 "$HOME/bidradar-runner.log" || true
  echo
  echo "== Done. The runner should show as 'Idle' on GitHub (Settings > Actions > Runners). =="
  echo "Keep the phone on the charger and Wi-Fi. Log: ~/bidradar-runner.log   Restart: bidradar-start"
}
main "$@" < /dev/null
