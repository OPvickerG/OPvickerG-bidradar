#!/bin/bash
# OPvickerG BidRadar — first-boot setup for an Oracle Cloud Always Free VM in India (Mumbai/Hyderabad).
# Paste into: Create instance > Show advanced options > Management > "Paste cloud-init script".
#
# GeM only accepts connections from Indian IP addresses, so the scanner runs here as a GitHub Actions
# self-hosted runner (label "india"). This script installs everything EXCEPT the runner registration, which
# needs a one-hour token from GitHub (see infrastructure/oracle/README.md). No secrets are stored here.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y python3 python3-venv python3-pip git curl jq tzdata ca-certificates
timedatectl set-timezone UTC || true

# 1 GB RAM on the free AMD shape: add 2 GB swap so pip installs never run out of memory
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Dedicated unprivileged user for the runner
id runner >/dev/null 2>&1 || useradd -m -s /bin/bash runner

# GitHub Actions runner (official release, SHA-256 verified against the release notes)
RUNNER_DIR=/home/runner/actions-runner
mkdir -p "$RUNNER_DIR" && cd "$RUNNER_DIR"
curl -fsSL -o /tmp/runner-release.json https://api.github.com/repos/actions/runner/releases/latest
VER=$(jq -r .tag_name /tmp/runner-release.json | sed 's/^v//')
case "$(uname -m)" in x86_64) ARCH=x64 ;; aarch64) ARCH=arm64 ;; *) echo "unsupported arch"; exit 1 ;; esac
PKG="actions-runner-linux-${ARCH}-${VER}.tar.gz"
curl -fsSL -o "$PKG" "https://github.com/actions/runner/releases/download/v${VER}/${PKG}"
EXPECTED=$(jq -r .body /tmp/runner-release.json | grep -E "BEGIN SHA linux-${ARCH} " | sed -E 's/.*-->([0-9a-f]{64})<!--.*/\1/' | head -1)
ACTUAL=$(sha256sum "$PKG" | cut -d' ' -f1)
if [ -z "$EXPECTED" ] || [ "$EXPECTED" != "$ACTUAL" ]; then echo "runner checksum mismatch or missing"; exit 1; fi
echo "runner $VER checksum verified"
tar xzf "$PKG" && rm -f "$PKG"
./bin/installdependencies.sh
chown -R runner:runner /home/runner

# Quick self-check written to the console log: can this machine reach GeM?
curl -s -o /dev/null -w "GeM reachability check: HTTP %{http_code} in %{time_total}s\n" --max-time 20 https://bidplus.gem.gov.in/all-bids || echo "GeM reachability check: FAILED"
echo "BIDRADAR_CLOUD_INIT_DONE"
