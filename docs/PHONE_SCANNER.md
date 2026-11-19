# India scanner on an Android phone (no card, no cost)

GeM only accepts connections from Indian internet addresses, and GitHub's runners are outside India. So an
Android phone on home Wi-Fi runs the scans as a **GitHub self-hosted runner** (label `india`):
- GitHub still schedules every :00 and :30 IST scan, keeps the logs and provides "Run workflow".
- Neon still stores the data.
- The phone only does the work.

## One-time setup on the phone (about 15 minutes, mostly waiting)

1. **Install Termux** from F-Droid (f-droid.org → search "Termux") or from github.com/termux/termux-app/releases.
   The old Google Play version is outdated. Optionally install **Termux:Boot** from the same place, so the
   scanner restarts after a phone reboot (open it once after installing).
2. **Phone settings → Apps → Termux → Battery → Unrestricted** (no battery optimisation). Otherwise Android stops
   it in the background.
3. Open Termux and type:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/OPvickerG/OPvickerG-bidradar/main/scripts/phone/setup.sh | bash
   ```

4. When it asks for the **runner token**:
   - Open github.com/OPvickerG/OPvickerG-bidradar/settings/actions/runners/new (any device, signed in to GitHub).
   - Copy the long code after `--token`. It's valid for 1 hour.
   - Paste it and press Enter.
5. Leave the phone **on the charger and Wi-Fi**. On GitHub → Settings → Actions → Runners, `android-phone` should
   show **Idle**.

## Day to day

- **Status:** GitHub → Settings → Actions → Runners (Idle/Active = OK, Offline = phone asleep or Termux closed).
- **Restart after a reboot (if Termux:Boot isn't installed):** open Termux and type `bidradar-start`.
- **Log:** `~/bidradar-runner.log` in Termux.
- **If the phone is off,** scheduled scans wait (one queued run). Slots that never ran are recorded as *missed*,
  and the next scan catches up deeper.

## Security

- **The runner only takes jobs from this repository.** The workflow gets the database key from GitHub per job
  (masked), and nothing secret is stored on the phone except the runner's own registration.
- **The repository requires approval before workflows from outside contributors run,** so strangers' pull requests
  can't execute code on the phone.
- **To remove it:** Settings → Actions → Runners → `android-phone` → Remove, then uninstall Termux.
