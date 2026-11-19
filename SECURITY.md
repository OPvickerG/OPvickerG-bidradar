# Security

## Secrets

- **No credentials exist in code, images or the repository.**
  - `.env.local`, used for local development only, is git- and docker-ignored.
  - Production secrets live in **Google Secret Manager** and are injected as environment variables into the
    Cloud Run service and job. Only their two service accounts get `secretAccessor`, per secret.
- **Secrets are never logged.** The JSON log formatter blanks any field whose name contains `password`, `secret`, `token`, `api_key`/`apikey`, `authorization` or `cookie`.
- **The admin password is stored only as a salted PBKDF2-SHA256 hash** (600,000 iterations). The deployment guide
  creates it at a hidden prompt.
- **The Resend API key should be created with *Sending access* only.**

## Access control

- **Single administrator account.**
  - Signed, HttpOnly, `SameSite=Lax`, `Secure` session cookie with a 12 h lifetime.
  - Sessions are invalidated when the password hash changes.
- **Login rate limit:** 5 failures per IP per 15 minutes (per instance; at most 2 instances).
- **CSRF token on every state-changing form.**
- **Security headers:**
  - a strict Content-Security-Policy (no inline script or style; `frame-ancestors 'none'`);
  - `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin`, HSTS.
- **Public endpoints are limited to:**
  - `/healthz`, which returns no data beyond the last scan time;
  - `/verify-email`, which needs a 256-bit single-use token, stored hashed, expiring in 48 h;
  - `/webhooks/resend`, which requires a Svix HMAC signature with a 5-minute timestamp tolerance;
  - `/internal/scan`, which is disabled unless a bearer token secret is set.
- **Least-privilege service accounts:**
  - The scheduler account can only invoke the scan job.
  - The dashboard account can only run that one job (it needs `run.developer` on that job alone, to pass the
    `SCAN_TRIGGER=manual` override) and read its secrets.

## Data protection

BidRadar stores public tender information only, and less of it than it reads:
- Buyer login ids returned by the GeM listing (`b.b_created_by`) are dropped before storage.
- Email addresses and phone numbers in tender PDFs are replaced with `<email-redacted>` / `<phone-redacted>`
  before any text is stored. Only redacted English text is kept (up to 20,000 characters per tender for
  re-scoring; 4,000 per document as an excerpt). PDFs themselves are not stored.
- Masked officer names on GeM documents are discarded.
- On result pages, losing bidders are reduced to counts and prices. The L1 seller name is stored only once GeM
  shows the bid as awarded (public award information).
- The recipient address is the only personal data the app holds about its user.

Database connections use TLS (`sslmode=require` on Neon; Cloud SQL connector otherwise). Neon and Cloud SQL
encrypt data at rest.

## Interaction with government portals

- **Read-only.** BidRadar never logs in, never submits forms other than the public search form that GeM's own page
  uses, and never bids. It changes no official record.
- **No CAPTCHA, OTP or login bypass.** Sources that require these are listed as `requires_captcha` /
  `requires_auth` and are not scraped.
- **Polite traffic.** At most one request per 3 s per portal, early stop when a page has nothing new, bounded
  pages/documents/refreshes per scan, retries with backoff, and a circuit breaker. A typical scan makes about
  40–150 requests.
- **Identifies itself.** The User-Agent identifies an automated monitoring client without exposing personal data.
- **If a portal blocks or throttles BidRadar, the source is marked `inaccessible`.** The right response is to
  reduce frequency or contact the portal, not to evade the block (no proxy rotation).

## Email

- Recipients must prove control of the address (verification link) before any alert is sent.
- The app records "delivered" only from the provider's signed delivery webhook, never from API acceptance.
- Email content is rendered with Jinja autoescaping, so portal text cannot inject HTML into emails.

## Reporting and patching

- **Dependencies are pinned in `requirements.txt`.** Rebuild periodically (`deploy.sh`) to pick up base-image
  security patches, and review `pip-audit` output before upgrading pins.
- **Logs live in Cloud Logging**, 30-day default retention.
