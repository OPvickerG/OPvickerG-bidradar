# India scanner host: Oracle Cloud Always Free (Mumbai)

**Why this exists:** GeM (bidplus.gem.gov.in, gem.gov.in) only accepts connections from Indian IP addresses. This
was verified on 19 Nov 2026 from 40 locations in 24 countries; all failed, and India succeeded. GitHub's own
runners are outside India, so the scan workflow runs on a self-hosted GitHub Actions runner on a free Oracle VM in
Mumbai. GitHub still provides the 30-minute schedule, the logs and the "Run workflow" button. Neon still holds the
data.

## Pieces

| Piece | Where |
|---|---|
| VM | Oracle Cloud, home region **India West (Mumbai)**, shape `VM.Standard.E2.1.Micro` (Always Free), Ubuntu 24.04 |
| First-boot setup | `cloud-init.sh`: Python, 2 GB swap, `runner` user, official GitHub runner (SHA-256 verified), GeM reachability check |
| Runner registration | `scripts/link_oracle_runner.sh <VM_IP>`: asks for the one-hour token from GitHub (hidden input) and installs the runner as a service |
| Workflow | `.github/workflows/scan.yml`, `runs-on: [self-hosted, india]` |
| Database access | Neon GitHub integration (`NEON_API_KEY` secret + `NEON_PROJECT_ID` variable). The URL is fetched per run and masked |

## Steps

1. Sign up at signup.cloud.oracle.com.
   - Billing country: India.
   - **Home region: India West (Mumbai)**. This can't be changed later. Hyderabad (India South) also works.
2. Compute → Instances → Create instance:
   - Image: Canonical Ubuntu 24.04. Shape: VM.Standard.E2.1.Micro (Always Free-eligible).
   - Networking: the default new VCN with a public IPv4 address.
   - SSH keys: paste the public key `~/.ssh/bidradar_oracle.pub`.
   - Advanced options → Management → cloud-init script: paste `cloud-init.sh`.
3. Wait about 5 minutes. Then confirm the setup finished and GeM is reachable:

   ```bash
   ssh -i ~/.ssh/bidradar_oracle ubuntu@<IP> 'grep -E "GeM reachability|BIDRADAR_CLOUD_INIT_DONE" /var/log/cloud-init-output.log'
   ```

4. Open GitHub repo → Settings → Actions → Runners → New self-hosted runner (Linux x64) and copy the token. Then run:

   ```bash
   scripts/link_oracle_runner.sh <IP>
   ```

5. Resume monitoring: dashboard Settings → Monitoring → Resume, or set `app_settings.monitoring.paused = false`.

## Security notes

- The VM has no inbound services apart from SSH (key-only). The runner makes outbound HTTPS connections only.
- **Self-hosted runners on public repositories can run code from fork pull requests.** Keep the repository private
  (self-hosted minutes are free), or set Settings → Actions → "Require approval for all external contributors".
- Always Free resources stay free; Oracle may reclaim idle Always Free compute. The 30-minute scans keep it active.
