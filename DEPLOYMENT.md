# Deployment guide (Google Cloud)

Expected time: 45–60 minutes. Everything runs in the browser through Google Cloud Shell, so nothing needs to be
installed on your computer.

> Do **not** paste passwords or API keys into source files, chat messages or a Git repository. Secrets go into
> Secret Manager, as in step 3.

## 0. What you need

| Item | Notes |
|---|---|
| Google account + Google Cloud project with **billing enabled** | Billing is required even when usage stays in free tiers. Set a budget alert (step 9). |
| PostgreSQL database | **Neon** free tier (recommended, see COST_ESTIMATE.md), or Cloud SQL (from about US$9/month). |
| Email provider (when you are ready for real alerts) | **Resend** free tier: 3,000 emails/month, 100/day. Needs a domain you control for the "from" address. |
| A domain | Optional for the dashboard (the Cloud Run URL works); required for Resend sending. |

## 1. Create the database (Neon)

1. Sign up at neon.tech and create a project in the region closest to India that Neon offers (check the region
   list when creating the project; for example AWS Asia Pacific Singapore).
2. Copy the **pooled** connection string. It looks like `postgresql://USER:PASSWORD@HOST/DB?sslmode=require`.
   Keep it in the Neon console or a password manager for now. You will paste it only into Secret Manager.

## 2. Open Cloud Shell and get the code

Open console.cloud.google.com, select your project and click **Activate Cloud Shell**. Then upload the
`OPVICKERG_BIDRADAR` folder (Cloud Shell menu → Upload, as a zip), or clone it from your private repository:

```bash
cd ~ && unzip OPVICKERG_BIDRADAR.zip && cd OPVICKERG_BIDRADAR
```

```bash
export PROJECT_ID=$(gcloud config get-value project) REGION=asia-south1
```

## 3. Create secrets (values typed at hidden prompts, never stored in files)

```bash
gcloud services enable secretmanager.googleapis.com
```

Database URL (paste the Neon string at the prompt, then press Enter and Ctrl-D):

```bash
gcloud secrets create bidradar-database-url --data-file=-
```

Random session secret:

```bash
openssl rand -hex 32 | tr -d '\n' | gcloud secrets create bidradar-session-secret --data-file=-
```

Admin password hash. The script prompts for a new password (at least 12 characters, not reused elsewhere) and
stores only the hash:

```bash
python3 -m core.passwords | tr -d '\n' | gcloud secrets create bidradar-admin-password-hash --data-file=-
```

(Optional, later) Resend credentials. See step 8.

## 4. Deploy

```bash
PROJECT_ID=$PROJECT_ID ./infrastructure/gcp/deploy.sh
```

The script:
- enables the required APIs;
- builds the image with Cloud Build;
- creates three least-privilege service accounts;
- deploys the dashboard service `bidradar-web` and the scanner job `bidradar-scan`;
- creates the Cloud Scheduler trigger `bidradar-scan-every-30min` (`0,30 * * * *`, Asia/Kolkata);
- prints the dashboard URL.

Database migrations run automatically when the dashboard starts.

Email stays in **console (dry-run) mode**: alerts are generated and stored, but nothing is sent.

## 5. Sign in

Open the printed URL and sign in as `admin` with the password from step 3.

## 6. Configure the recipient (when you decide the address)

1. Go to **Settings → Alert recipient**, enter the address and click *Save & send verification*.
2. In dry-run mode, the dashboard shows the verification link instead of emailing it. After step 8 it is emailed.
3. Until the link is clicked, every alert is stored with status `held_unverified` and nothing is sent.
4. After verification, the newest 20 held alerts are released. Older ones are marked cancelled and stay visible in
   the Monitoring Centre.
5. Use **Send test notification** to confirm the layout reaches the inbox.

## 7. Verify scanning (do this right after the first deploy)

Run one scan now and watch it:

```bash
gcloud run jobs execute bidradar-scan --region $REGION --wait
```

Then run the read-only health checks:

```bash
PROJECT_ID=$PROJECT_ID ./infrastructure/gcp/verify.sh
```

In the dashboard, the Monitoring Centre should show a `manual` or `scheduler` row with status `succeeded` and a
non-zero *Listings* count, and the GeM source should show a recent *Last success*.

**Important check:** if the GeM source shows connection timeouts, GeM is not reachable from Google Cloud's
Mumbai IP range. This happened from the development machine on 18 Nov 2026. See RUNBOOK.md, section "Portal
unreachable", before changing anything.

At the next :00 or :30 IST, a `scheduler` row should appear on its own. Close your laptop and check again later:
rows keep appearing because everything runs in Google Cloud.

## 8. Turn on real email (Resend)

1. Create a Resend account. Add and verify your sending domain by adding the DNS records Resend shows, at your DNS
   provider.
2. Create an API key with *Sending access* only. Store it with:

   ```bash
   gcloud secrets create bidradar-resend-api-key --data-file=-
   ```

3. In Resend → Webhooks, add `https://<dashboard-url>/webhooks/resend` for the events `email.delivered`,
   `email.bounced`, `email.complained` and `email.delivery_delayed`. Copy the signing secret (`whsec_…`) and store it:

   ```bash
   gcloud secrets create bidradar-resend-webhook-secret --data-file=-
   ```

4. Redeploy with the provider switched on:

   ```bash
   EMAIL_PROVIDER=resend EMAIL_FROM="BidRadar <alerts@your-domain>" PROJECT_ID=$PROJECT_ID ./infrastructure/gcp/deploy.sh
   ```

5. Click **Resend verification** in Settings, verify the address, then send a test notification. In the Monitoring
   Centre, the test should move to `accepted` and then to `delivered` when Resend's webhook arrives.

SMTP alternative: set `EMAIL_PROVIDER=smtp` and the `SMTP_*` variables. SMTP has no delivery webhook, so messages
stay `accepted`.

## 9. Costs and budget alert

Billing → Budgets & alerts → create a budget of, for example, ₹500/month with alerts at 50/90/100%. See
COST_ESTIMATE.md for what to expect.

## Pause, resume, stop

- **Pause scanning (keeps everything):** Settings → Monitoring → *Pause*. Scheduled slots are recorded as
  `skipped_paused`. Alternatively, pause the trigger itself:

  ```bash
  gcloud scheduler jobs pause bidradar-scan-every-30min --location $REGION
  ```

  Resume it with `gcloud scheduler jobs resume …`.
- **Stop email only:** Settings → Notifications → untick *Email alerts enabled*. Alerts are held, not lost.
- **Remove everything:** delete the Cloud Run service, job, scheduler job, Artifact Registry repo and secrets, then
  delete the Neon project.

## Updating

Re-run `deploy.sh`. It builds a new image tag and updates the service, job and scheduler in place. Database
migrations apply automatically.

## Custom domain (optional)

Cloud Run → `bidradar-web` → *Manage custom domains*, or put it behind a load balancer. Then update
`PUBLIC_BASE_URL` (re-run `deploy.sh`, or `gcloud run services update … --update-env-vars`) so links in emails use
the new domain. Update the Resend webhook URL as well.
