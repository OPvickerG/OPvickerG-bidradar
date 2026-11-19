#!/bin/bash
# Runs inside the proot Ubuntu on the phone (called by setup.sh). Installs Python and the official GitHub
# Actions runner (checksum-verified) and registers it once with a token typed by the phone's owner.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
REPO_URL="https://github.com/OPvickerG/OPvickerG-bidradar"

apt-get install -y -qq python3 python3-venv python3-pip git jq tzdata >/dev/null

RUNNER_DIR=/root/actions-runner
mkdir -p "$RUNNER_DIR" && cd "$RUNNER_DIR"
if [ ! -x ./run.sh ]; then
  case "$(uname -m)" in
    aarch64|arm64) ARCH=arm64 ;;
    armv7l|armv8l|arm) ARCH=arm ;;
    x86_64) ARCH=x64 ;;
    *) echo "Unsupported phone CPU: $(uname -m)"; exit 1 ;;
  esac
  curl -fsSL -o /tmp/runner-release.json https://api.github.com/repos/actions/runner/releases/latest
  VER=$(jq -r .tag_name /tmp/runner-release.json | sed 's/^v//')
  PKG="actions-runner-linux-${ARCH}-${VER}.tar.gz"
  echo "   downloading GitHub runner $VER ($ARCH)"
  curl -fsSL -o "$PKG" "https://github.com/actions/runner/releases/download/v${VER}/${PKG}"
  EXPECTED=$(jq -r .body /tmp/runner-release.json | grep -E "BEGIN SHA linux-${ARCH} " | sed -E 's/.*-->([0-9a-f]{64})<!--.*/\1/' | head -1)
  ACTUAL=$(sha256sum "$PKG" | cut -d' ' -f1)
  if [ -z "$EXPECTED" ] || [ "$EXPECTED" != "$ACTUAL" ]; then echo "Runner checksum mismatch - stopping"; exit 1; fi
  echo "   checksum verified"
  tar xzf "$PKG" && rm -f "$PKG"
  ./bin/installdependencies.sh >/dev/null
fi

echo -n "   GeM check from this phone: "
curl -s -o /dev/null -w "HTTP %{http_code} in %{time_total}s\n" --max-time 20 https://bidplus.gem.gov.in/all-bids || echo "FAILED (check Wi-Fi)"

if [ ! -f .runner ]; then
  echo
  echo "Open $REPO_URL/settings/actions/runners/new on any device,"
  echo "copy the long code that comes after --token, and paste it here."
  read -r -s -p "Runner token: " T < /dev/tty; echo
  RUNNER_ALLOW_RUNASROOT=1 ./config.sh --unattended --url "$REPO_URL" --token "$T" \
    --name "android-phone" --labels india --replace
fi
echo "   runner registered"
