# Cost estimate

## Zero-cost option (recommended without a budget): US$0/month, no card

See [DEPLOYMENT_FREE.md](DEPLOYMENT_FREE.md).

| Part | Free allowance (checked 19 Nov 2026) | BidRadar usage |
|---|---|---|
| GitHub Actions, **public** repo | standard runners free for public repositories ([billing docs](https://docs.github.com/en/billing/concepts/product-billing/github-actions)) | about 1,440 runs × 3–8 min |
| Neon Free | 100 CU-hours + 1 GB per project; no card; overages blocked, not billed ([Neon FAQ](https://neon.com/faqs/free-plan-limits-and-quotas)) | about 40–70 CU-hours; storage grows about 200 MB/year |
| Render Free web service | sleeps after 15 min idle ([Render docs](https://render.com/docs/free)); some accounts are asked for a card for verification | a few hours/month |
| Resend Free | 100/day, 3,000/month; without a domain, sends only to your own address ([Resend KB](https://resend.com/docs/knowledge-base/403-error-resend-dev-domain)) | under 300/month |

Limits to watch:
- Neon compute-hours. If they run out, scans fail until the 1st of the month, and the failures are visible.
- GitHub's 60-day inactivity rule, handled by `keepalive.yml`.

## Google Cloud option

**Bottom line:** expect about **US$0.5–8 per month (roughly ₹40–700)** with the recommended setup (Cloud Run + Cloud
Scheduler + Neon free + Resend free). The main variable is how long each scan runs, because a Cloud Run Job is
billed for its whole run, including the polite pauses between requests.

> Prices and free tiers change. Check each provider's pricing page before deploying, and set a budget alert
> (DEPLOYMENT.md, step 9). Figures below were collected during Phase 3 research in November 2026. Items marked
> **verify** could not be re-confirmed on the provider's page during this build.

## Workload assumptions

- 48 scans/day × 30 days = **1,440 scan executions/month**.
- Typical scan: about 30 search terms × 1–2 pages, some documents and refreshes, 3 s apart. That is
  **3–8 minutes** at 1 vCPU / 1 GiB. The Monitoring Centre shows real durations after deployment.
- Dashboard: occasional use, scales to zero.
- Email: a few alerts per day, plus optional summaries. Well under 100/day.

## Per service

| Service | Free allowance | Expected usage | Expected cost |
|---|---|---|---|
| **Cloud Run Job** (scanner) | about 240,000 vCPU-s + 450,000 GiB-s per month per billing account (**verify** for asia-south1) | 1,440 × 3–8 min ≈ 260,000–690,000 vCPU-s | **US$0.5–8** (see sensitivity below) |
| **Cloud Run service** (dashboard) | shared with the above | a few minutes/day | ~US$0 |
| **Cloud Scheduler** | first 3 jobs per billing account free, then US$0.10/job/month (**verify**) | 1 job | US$0–0.10 |
| **Neon Postgres** (free plan) | 100 CU-hours/month per project, 0.5–1 GB storage depending on plan revision (**verify**), autosuspend after 5 min idle | Awake during scans: about 50–80 CU-hours at 0.25 CU | US$0 while within limits |
| **Secret Manager** | 6 active secret versions + 10,000 access operations/month ([pricing](https://cloud.google.com/secret-manager/pricing)) | 3–5 secrets; about 3,000 accesses (one per job start) | US$0 |
| **Artifact Registry** | 0.5 GB storage (**verify**) | ~250 MB per image; delete old tags | US$0–0.10 |
| **Cloud Build** | free build-minutes each month (**verify**) | a few builds per month | US$0 |
| **Cloud Logging** | 50 GiB/month ingestion | well under 1 GiB | US$0 |
| **Resend** | 3,000 emails/month, 100/day | < 300/month | US$0 |
| **Total** | | | **about US$0.5–8/month** |

### Scanner run-time sensitivity (the one number that matters)

| Average scan duration | vCPU-seconds/month | Above about 240k free | Approx. cost at about US$0.000018/vCPU-s + memory |
|---|---|---|---|
| 3 min | 259,200 | ~19k | **≈ US$0.4** |
| 5 min | 432,000 | ~192k | **≈ US$3.5** |
| 8 min | 691,200 | ~451k | **≈ US$8** |

Ways to lower it, with trade-offs:
- `SCANNER_REQUEST_DELAY=2`: about one third faster, still polite.
- Fewer search terms. Disable categories you don't need, or remove overlapping portal search terms in Settings.
- `SCANNER_MAX_PAGES_PER_TERM=1`: paging already stops early when nothing is new.
- Lower `SCANNER_MAX_DOCUMENTS_PER_SCAN` / `SCANNER_MAX_REFRESHES_PER_SCAN`.

## When you would pay more

- **Cloud SQL instead of Neon:** the smallest shared-core instance runs about US$9–12/month all the time. It's worth
  it only if Neon's compute-hours run out.
- **Neon limits.** If the free compute allowance is exceeded, Neon suspends compute until the next month, so
  scans would fail and be visible as `failed`. Upgrade to Neon's paid plan, or reduce scan duration.
- **Storage growth.**
  - Each analysed tender keeps about 25 KB of redacted text, so roughly 20 analysed tenders/day comes to about
    180 MB/year.
  - Irrelevant listings are small (about 2 KB).
  - The 0.5–1 GB free storage should last more than a year. Old closed tenders can be archived if needed (see
    RUNBOOK.md).
- **Resend.** More than 100 emails/day or 3,000/month needs Resend's paid plan (from about US$20/month).
- **Optional OCR or embeddings.** These add image size and memory (1–2 GiB), which raises job cost
  proportionally.

## Costs that do not exist

- No always-on VM.
- No load balancer (the Cloud Run URL is used directly).
- No paid scraping or proxy services.
- No paid AI APIs: matching runs locally and deterministically.
