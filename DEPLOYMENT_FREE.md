# Zero-cost deployment (no credit card)

This is the recommended setup when there is no budget. Every part runs in the cloud on free plans, so your
computer can be off.

| Part | Provider (free plan) | Card needed? | Notes |
|---|---|---|---|
| Scanner, every 30 min | **GitHub Actions** in a **public** repository | No | Standard runners are free for public repos. A private repo's 2,000 free minutes/month is not enough for 48 scans/day. |
| Database | **Neon** Free | No | 100 compute-hours + 1 GB per project; over the limit, Neon blocks rather than bills |
| Dashboard | **Render** Free web service | Usually no (see note) | Sleeps after 15 min idle; the first page load after that takes about a minute |
| Email (later) | **Resend** Free | No | 100/day, 3,000/month. Without your own domain it can only send **to the address you signed up with**, which is fine for alerts to yourself |

**What "public repository" means:** anyone can read the code and the scan logs on GitHub. The logs contain only
public tender information and errors. Secrets (database URL, API keys) are stored as encrypted GitHub secrets and
are masked in logs, so they are never visible.

**Known trade-offs of free hosting:**
- **GitHub may start scheduled runs late** (often 5–20 minutes at busy times) and occasionally drops one. BidRadar
  records each run against its intended :00/:30 slot, so a late run doesn't create a false "missed" row. A dropped
  run is recorded as *missed*, and the next run catches up.
- **GitHub disables schedules in public repos after 60 days without activity.** The included `keepalive.yml`
  workflow commits a timestamp monthly to prevent that.
- **Render may ask some accounts for a card as an anti-abuse check.** If it does, stop and use the fallback at the
  end of this guide.
- **GeM reachability from GitHub's servers (outside India) is not yet confirmed.** Step 5 checks it.

---

## Step 1: GitHub account and repository (5 min)

1. Sign up at github.com (free) if you don't have an account.
2. Create a new repository: **New → name `opvickerg-bidradar` → Public → do not add a README** → Create.
3. Keep the page open. You'll need its URL (`https://github.com/<you>/opvickerg-bidradar.git`).

## Step 2: Neon database (5 min)

1. Sign up at neon.tech. "Continue with GitHub" works, and no card is needed.
2. Create a project. Pick the region closest to India in the list (for example AWS Asia Pacific, Singapore).
3. On the dashboard click **Connect**, tick **Connection pooling**, and copy the connection string
   (`postgresql://…?sslmode=require`). Keep it in Neon or a password manager. You'll paste it only into GitHub and
   Render secret fields, never into a file or chat.

## Step 3: Upload the code (5 min)

The code is ready as a git repository on your Mac. In Terminal:

```bash
cd ~/Gem/OPVICKERG_BIDRADAR
```

```bash
git remote add origin https://github.com/<you>/opvickerg-bidradar.git
```

```bash
git push -u origin main
```

`git push` asks for your GitHub username and a password. For the password, use a **personal access token**: GitHub
→ Settings → Developer settings → Personal access tokens → Fine-grained → *Generate*. Give it access only to this
repository, with **Contents: Read and write** and **Workflows: Read and write**. Type the token at the prompt in
your own Terminal; don't share it anywhere.

## Step 4: Add the database secret to GitHub (2 min)

In the repository: **Settings → Secrets and variables → Actions**.
- **Secrets → New repository secret:** name `DATABASE_URL`, value: the Neon connection string.
- **Variables → New repository variable:** name `EMAIL_PROVIDER`, value `console` (dry-run: nothing is emailed yet).

## Step 5: First cloud scan and the GeM check (10 min)

1. Open the **Actions** tab and allow workflows if GitHub asks.
2. Choose **BidRadar scan → Run workflow → manual → Run workflow**.
3. Open the run and the **Scan** step. The last log line, `"scan finished"`, shows `status` and `new`.
   - `succeeded` or `partial` with `new` > 0 means GeM is reachable from GitHub. The scheduled runs at every :00
     and :30 IST now take over by themselves.
   - `failed` with `ConnectTimeout` errors means GeM does not accept connections from GitHub's servers. Stop here
     and tell me; we'll look at other free options rather than evading the block.

## Step 6: Dashboard on Render (10 min)

1. On this Mac, create the admin password hash. Choose a new password of at least 12 characters:

   ```bash
   cd ~/Gem/OPVICKERG_BIDRADAR && python3 -m core.passwords
   ```

   Copy the printed line, which starts `pbkdf2_sha256$`. It is a hash, not your password.
2. Sign up at render.com with **GitHub**. Then **New → Blueprint**, select the `opvickerg-bidradar` repository and
   apply. Render reads `render.yaml`.
3. Render asks for the values marked secret:
   - `DATABASE_URL`: the Neon string.
   - `ADMIN_PASSWORD_HASH`: the line from step 6.1.
   - `PUBLIC_BASE_URL`: leave blank for now.
   - Leave the others blank.
4. When the deploy finishes, copy the service URL (`https://bidradar-web-xxxx.onrender.com`). Then:
   - Render → service → **Environment**: set `PUBLIC_BASE_URL` to that URL and save.
   - GitHub → Settings → Variables: add `PUBLIC_BASE_URL` with the same URL, so email links point to the
     dashboard.
5. Open the URL and sign in as `admin` with your password. The Monitoring Centre should list the scan from step 5.

**If Render asks for a credit card,** don't enter one. Tell me, and we'll use the fallback: the scanner and
database keep working without any dashboard host, and I'll set up a no-card alternative for the dashboard.

## Step 7 (optional): "Scan now" button

Create another fine-grained token with access to this repository only and **Actions: Read and write**. In Render →
Environment set:
- `GITHUB_REPO` = `<you>/opvickerg-bidradar`
- `GITHUB_DISPATCH_TOKEN` = the token

## Step 8 (later): real email alerts

1. Sign up at resend.com **with the email address that should receive alerts**. Without your own domain, Resend
   can only deliver to that address.
2. Create an API key with *Sending access* and add it as:
   - GitHub secret `RESEND_API_KEY`;
   - Render environment `RESEND_API_KEY`.
3. Set `EMAIL_PROVIDER` = `resend` and `EMAIL_FROM` = `BidRadar <onboarding@resend.dev>` in both places (GitHub
   *variables*, Render *environment*).
4. In Resend → Webhooks, add `<your Render URL>/webhooks/resend` with events `email.delivered`, `email.bounced`,
   `email.complained`. Put its signing secret in Render's `RESEND_WEBHOOK_SECRET`.
5. In the dashboard: **Settings → Alert recipient** → enter that same address → click the link in the verification
   email → **Send test notification**.

Resend recommends your own domain for production sending. If you later buy a domain (about ₹800/year), verify it
in Resend and change `EMAIL_FROM`; nothing else changes.

## Pause, resume, check

- **Pause scanning:** dashboard Settings → Monitoring → Pause. Alternatively, in GitHub Actions → BidRadar scan →
  ··· → *Disable workflow*.
- **Is it running?** GitHub Actions tab: a run about every 30 minutes. Dashboard Monitoring Centre: rows marked
  succeeded, partial, failed or missed.
- **Limits:**
  - Neon → Usage shows compute-hours. Scans use roughly 40–70 of the 100 hours/month.
  - Render free gives 750 instance-hours/month, and a sleeping service uses none.

## Fallback without a dashboard host

The scanner (GitHub Actions) and database (Neon) need no dashboard to work. Alerts are queued in the database and
sent by the scanner run. Only the web pages need a host.
