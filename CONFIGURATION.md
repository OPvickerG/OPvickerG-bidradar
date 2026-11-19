# Configuration

There are two layers of configuration:
1. **Environment variables.** These are set on Cloud Run by `deploy.sh`; secrets come from Secret Manager.
2. **Dashboard settings.** These are stored in the database (`app_settings`) and take effect on the next scan
   without a redeploy.

## Environment variables

| Variable | Secret? | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | **yes** | `sqlite:///./bidradar.db` | PostgreSQL URL (`postgres://`, `postgresql://` accepted; psycopg 3 driver used) |
| `SESSION_SECRET` | **yes** | random per process (dev only) | Signs session cookies. Required when `APP_ENV=production` |
| `ADMIN_USERNAME` | no | `admin` | Dashboard username |
| `ADMIN_PASSWORD_HASH` | **yes** | — | PBKDF2 hash from `python3 -m core.passwords`. Sign-in is disabled if unset |
| `APP_ENV` | no | `development` | `production` enforces `SESSION_SECRET` |
| `PUBLIC_BASE_URL` | no | `http://localhost:8080` | Used in email links and verification links |
| `SECURE_COOKIES` | no | `true` | HTTPS-only cookies plus HSTS. Set to `false` only for local http |
| `EMAIL_PROVIDER` | no | `console` | `console` (dry-run, nothing sent) \| `resend` \| `smtp` |
| `EMAIL_FROM` | no | — | Sender, e.g. `BidRadar <alerts@your-domain>` (domain must be verified with the provider) |
| `RESEND_API_KEY` | **yes** | — | Resend API key (sending access only) |
| `RESEND_WEBHOOK_SECRET` | **yes** | — | `whsec_…` signing secret; without it, webhook calls are rejected |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME` | no | —, 587, — | SMTP alternative (STARTTLS) |
| `SMTP_PASSWORD` | **yes** | — | SMTP password |
| `EMAIL_MAX_ATTEMPTS` | no | 5 | Send attempts before an alert is marked `failed` |
| `SCANNER_REQUEST_DELAY` | no | 3.0 | Minimum seconds between requests to one portal |
| `SCANNER_REQUEST_TIMEOUT` | no | 60 | Read timeout (connect timeout is capped at 15 s) |
| `SCANNER_MAX_PAGES_PER_TERM` | no | 2 | Pages per search term per scan (paging stops early when nothing is new) |
| `SCANNER_BACKFILL_PAGES_PER_TERM` | no | 4 | Pages per term on the first scan after missed slots |
| `SCANNER_MAX_DOCUMENTS_PER_SCAN` | no | 40 | Tender PDFs downloaded per scan |
| `SCANNER_MAX_REFRESHES_PER_SCAN` | no | 40 | Tracked tenders re-checked per scan |
| `SCAN_LEASE_MINUTES` | no | 25 | Lock lease; the scan's time budget is the lease minus 4 minutes |
| `SOURCE_FAILURE_THRESHOLD` | no | 3 | Consecutive request failures before a source is skipped for that scan |
| `EMBEDDING_BACKEND` | no | `none` | `fastembed` adds embedding similarity (needs the `fastembed` package) |
| `INTERNAL_TASK_TOKEN` | **yes** | — | Enables `POST /internal/scan` with `Authorization: Bearer …` (optional alternative trigger) |
| `GCP_PROJECT`, `GCP_REGION`, `SCAN_JOB_NAME` | no | — | Let the dashboard's *Scan now* start the Cloud Run Job |

To change a secret, add a new version (for example `gcloud secrets versions add bidradar-session-secret
--data-file=-`), then redeploy or create a new revision. Changing `ADMIN_PASSWORD_HASH` signs out all sessions.

## Dashboard settings

| Setting | Where | Default | Effect |
|---|---|---|---|
| Alert recipient | Settings → Alert recipient | **unset** | One address. It must be verified via the emailed link (expires in 48 h). Until then alerts are `held_*` |
| Monitoring paused | Settings → Monitoring | active | Scheduled slots become `skipped_paused`; manual *Scan now* still works |
| Alert threshold | Settings → Matching | 75 | Minimum score for alerts. All tenders remain in the Explorer |
| Enabled categories | Settings → Matching | A–F | Disabled categories are not scored or searched; stored tenders are re-scored |
| Keywords | Settings → Keywords | seed file | Add, disable or delete (user-added only). Types: exact phrase, synonym, portal search term, concept object, concept action, exclusion |
| Geography | Settings → Geography | all India | States and/or PIN prefixes. A tender with no location is excluded only when a filter is set |
| Sources | Settings → Sources | GeM on, CPPP mirrors on (unreliable) | Toggle implemented sources. CAPTCHA/login sources cannot be enabled |
| Event types | Settings → Notifications | all on | New tender, deadline change, corrigendum, eligibility change, cancellation, outcome |
| Daily / weekly summary | Settings → Notifications | off | Sent at the chosen IST hour (and weekday) at most once per period |

Watched tenders (★ on the tender page) receive change alerts even when they score below the threshold.

## Seed data

`database/seed/categories.yaml` defines the six categories:
- phrases, synonyms and discovery (portal search) terms;
- concept objects and actions (each optionally with a `weight`);
- exclusions, and whether the supply-only penalty applies.

It is loaded idempotently at every scan, so new seed keywords appear automatically. Dashboard edits are never
overwritten.
