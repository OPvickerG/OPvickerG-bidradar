# Operations runbook

## Daily glance (1 minute)

Open the dashboard Overview:
- **Scan strip.** The last scan should be at most 30 minutes old. A red banner appears if no scan has succeeded
  in 75 minutes.
- **KPIs.** New relevant tenders, closing within 3 days, alerts held.
- **Sources card.** Green dot = healthy.

## Scan statuses

| Status | Meaning | Action |
|---|---|---|
| succeeded | all enabled sources scanned without errors | none |
| partial | at least one source worked; some errors (e.g. one PDF unavailable, one source down) | look at *Recent errors*; usually transient |
| failed | no source could be scanned, or the scan crashed | see below; Cloud Run retries the task once automatically |
| missed | no execution was recorded for that slot | check Cloud Scheduler (`verify.sh`) and Cloud Run Job executions |
| skipped_locked | previous scan still running | fine occasionally; if frequent, reduce scan work (CONFIGURATION.md) |
| skipped_paused | monitoring paused in Settings | resume when ready |

## Portal unreachable (connection timeouts)

Symptom: the GeM source shows `ConnectTimeout` errors and scans become `failed`. After 6 failed scans in a row the
source is marked `inaccessible`.
1. Check whether gem.gov.in works in a normal browser. If GeM itself is down, wait. Scans recover automatically,
   and the first successful scan resets the failure count.
2. If GeM works in a browser but never from BidRadar, GeM may be throttling the cloud IP:
   - Raise `SCANNER_REQUEST_DELAY` (e.g. 6).
   - Lower `SCANNER_MAX_PAGES_PER_TERM` to 1.
   - Disable unused categories.
   - Redeploy, and leave it alone for a few hours.
3. Do **not** route traffic through proxies or rotate IPs to get around a block.

## Parser broke (portal changed its page)

Symptom: errors like "GeM listing token not found" or `KeyError`, with all scans failed. Capture one example of
the new response, update the adapter, add it as a test fixture, run `pytest`, and redeploy.

## Alerts not arriving

- **Monitoring Centre → Email notifications:**
  - `held_no_recipient` / `held_unverified`: configure or verify the recipient.
  - `held_disabled`: email alerts are switched off in Settings → Notifications.
  - `failed`: open the row for the provider's error (bad API key, unverified sending domain, quota).
  - `accepted` but never `delivered`: check the Resend dashboard and the webhook configuration (URL and secret).
  - `bounced`: the address rejects mail; fix the recipient.
- A tender alert is created only if:
  - its score is at or above the threshold;
  - its category is enabled;
  - it passes the geography filter;
  - it is still open.

  The tender page shows the alerts it generated.

## Manual scan

Dashboard → Monitoring Centre → **Scan now**, or:

```bash
gcloud run jobs execute bidradar-scan --region asia-south1 --wait
```

## Backups and restore

- **Neon:** the free plan keeps point-in-time history for a short window (check the current plan). For a portable
  backup, from Cloud Shell:

  ```bash
  pg_dump "$(gcloud secrets versions access latest --secret bidradar-database-url)" -Fc -f bidradar-$(date +%F).dump
  ```

  Store the dump in a private Cloud Storage bucket.
- **Restore:**

  ```bash
  pg_restore --clean --no-owner -d "<new database url>" bidradar-YYYY-MM-DD.dump
  ```

  Then update the `bidradar-database-url` secret and redeploy.

## Data retention (if storage approaches the free limit)

Irrelevant listings (score < 25) that closed more than 180 days ago can be deleted safely. History and matches
cascade with them:

```sql
DELETE FROM tenders WHERE relevance_score < 25 AND status <> 'active' AND last_seen_at < now() - interval '180 days';
```

## Rotating secrets

Add a new secret version (`gcloud secrets versions add …`), then re-run `deploy.sh` so new revisions pick it up.
After rotating `ADMIN_PASSWORD_HASH`, all sessions sign out.
