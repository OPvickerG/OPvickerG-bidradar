# Hybrid scanning: Mac when awake, server always

| | Mumbai server (Voroa, always on) | Mac "fast scanner" (when awake) |
|---|---|---|
| Role | `SCAN_ROLE=server` (default) | `SCAN_ROLE=helper` |
| When it scans | every :00/:30 IST; waits `HELPER_GRACE_MINUTES` (4) if the Mac checked in within 3 min | the moment each slot opens |
| Sends email | yes, plus a queue run every 2 minutes | never (`SEND_EMAIL=false`), it only queues |
| Lock lease | 8 min, renewed every minute | 5 min, renewed every minute |

How it fails safe:
- **The Mac is asleep or offline.** Its heartbeat goes stale, so the server scans on time.
- **The Mac sleeps mid-scan.** Its lease stops being renewed and expires within 5 minutes. The server keeps retrying every minute until the slot's window closes, then takes over the slot (a `scan_taken_over` event is recorded). When the Mac wakes and sees that it lost the lease, it stops without overwriting the server's result.
- **The Mac's scan fails.** The slot is marked `failed`, and the server re-runs it after its grace period.
- **Both machines try at once.** The database lock lets only one of them scan; the other one sees `already_ran`.

All-bids sweep: each scan also reads the newest pages of all open GeM bids (`SCANNER_SWEEP_PAGES`: 12 on the server, 20 on the Mac). Every new bid is scored. Only bids scoring at least `SCANNER_SWEEP_KEEP_MIN_SCORE` (50) are stored, which keeps the free database small. The last 6000 bid numbers are remembered, so each bid is scored only once.

Install on the Mac: `bash scripts/mac/install_helper.sh`. It asks once for the Neon `DATABASE_URL` and stores it in the login Keychain, then installs a launchd agent that starts at login and restarts itself if it stops.

Remove it: `bash scripts/mac/uninstall_helper.sh`.

Log: `~/Library/Logs/bidradar-helper.log`.

Keep the Mac's copy of the code the same as GitHub's. The matching rules are fingerprinted, and two different versions would keep re-scoring each other's results.

## Full GeM crawl (nothing open is left unchecked)
After its regular work, each scan spends `DEEP_CRAWL_MINUTES` (default 8) walking the complete list of open GeM bids. There are about 45,000, so about 4,500 pages. It saves its place in app_settings `deep_crawl`, so the next scan picks up where this one stopped. One full pass takes roughly 12–14 hours, so every open bid is re-checked about twice a day.
- **No skips.** The list is read latest-closing-first: new bids enter near the front and closed bids leave from the end, so listings only ever move to later pages. Each slice also re-reads one page of overlap. A bid can be read twice in a pass, never skipped.
- **Back-off.** If GeM refuses connections, the crawl pauses for `DEEP_CRAWL_PAUSE_HOURS` (2) and the regular scans carry on.
- **What's stored.** Every bid is scored. Bids scoring 40 or more are stored and their documents read, which can raise the score. Bids already stored go through change detection.
- **Progress.** Monitoring shows how far the current pass has got, when the last full pass finished, and how many bids it checked.

## "Check a bid" (top of every page)
Paste any GeM bid or RA number. The app looks it up live on GeM, stores it, reads its main document, scores it, and opens it with the explanation.
- If it matches and the scans had not found it, it is logged as a miss: a `missed_bid_found` event, counted on Monitoring. It is then alerted like any new match.
