# OPvickerG BidRadar

Autonomous, cloud-hosted monitoring of Indian government tenders for six service categories. BidRadar checks the
supported portals every 30 minutes (:00 and :30 IST), scores every listing for relevance, reads the tender
documents of likely matches, tracks changes, and emails a verified recipient.

| Code | Category |
|---|---|
| A | CRDI & diesel injection (FIP, injectors, common rail) |
| B | Cylinder head work (facing, valve seats, reconditioning) |
| C | Engine machining (boring/reboring, crankshaft grinding, overhaul) |
| D | Automotive scanning & diagnostics (OBD, ECU) |
| E | Plates facing & grinding (pressure plates, flywheels, surface grinding) |
| F | Car & generator repair (vehicles, DG sets) |

> **Status (19 Nov 2026):** built and tested locally (90 automated tests passing). **Not deployed yet.**
> Two deployment paths are ready:
> - **[DEPLOYMENT_FREE.md](DEPLOYMENT_FREE.md): US$0, no credit card.** GitHub Actions scanner, Neon database,
>   Render dashboard. This is the recommended path.
> - [DEPLOYMENT.md](DEPLOYMENT.md): Google Cloud. Needs a billing account (card), about US$0.5–8/month.
>
> **19 Nov 2026 update:** the code is on GitHub (OPvickerG/OPvickerG-bidradar) with a Neon database connected.
> The first cloud scan ran, but **GeM refuses all connections from outside India** (tested from 24 countries).
> The scanner therefore runs on an **Android phone in India** as a GitHub self-hosted runner (see docs/PHONE_SCANNER.md).
> No alert recipient is configured and no real email has been sent.

## What it does

- **Scans on a schedule.** A Cloud Scheduler cron (`0,30 * * * *`, Asia/Kolkata) starts a Cloud Run Job.
  - A database lease lock prevents overlapping scans.
  - Slots that never ran are recorded as *missed*, and the next scan pages deeper to catch up.
- **Discovers tenders on supported sources.** GeM BidPlus is searched with 30 category search terms; the GeM-hosted
  CPPP mirrors are flagged unreliable. Sources that need CAPTCHA, login or OTP are listed but never scraped (see
  [docs/SOURCE_COVERAGE.md](docs/SOURCE_COVERAGE.md)).
- **Scores each listing from 0 to 100** for each category. Signals are:
  - exact phrases and synonyms;
  - fuzzy matching and conservative spelling correction;
  - object + action concept proximity;
  - evidence from the tender document;
  - an optional embedding model.

  Penalties apply to unrelated context, to supply-only purchases and to equipment purchases. Every score comes
  with a plain-English explanation ([docs/MATCHING_RULES.md](docs/MATCHING_RULES.md)).
- **Reads tender PDFs** for listings scoring 40 or more. It extracts:
  - estimated value, EMD and ePBG;
  - turnover and experience requirements;
  - contract period and consignee PIN codes (used to infer the state).

  Contact details are redacted. Anything not printed in the document stays empty (null). Nothing is guessed.
- **Tracks changes.** It detects:
  - deadline changes, corrigenda and cancellations;
  - eligibility changes;
  - reverse auctions;
  - published results. Losing bidders are never stored by name.
- **Sends email.** The queue has:
  - dedupe keys, and retries with exponential backoff;
  - holds for alerts while no verified recipient exists;
  - optional daily and weekly summaries.

  "Delivered" is recorded only when the provider's signed webhook confirms delivery.
- **Provides a dashboard:**
  - Overview;
  - Tender Explorer (filters and sorting);
  - tender detail with score breakdown and change history;
  - Monitoring Centre (scans, errors, sources, mail log);
  - Settings (recipient + verification, threshold, categories, keywords, geography, sources, notification
    preferences, pause/resume, test email).

## Repository layout

```
backend/                 FastAPI app (dashboard, JSON API, auth, webhook, manual scan trigger)
frontend/                Jinja templates + CSS (no JavaScript; strict CSP)
scanner/
  scheduler/             entrypoint run.py (Cloud Run Job), lease lock, missed-slot recovery
  discovery/             pipeline.py — one scan end to end
  source_adapters/       GeM BidPlus, CPPP mirrors, registry of unsupported sources
  keyword_engine/        normalisation, stemming, phrase/synonym/fuzzy matching
  semantic_matching/     object+action concept proximity, optional embeddings
  document_processing/   PDF text extraction, redaction, optional OCR
  change_detection/      listing/corrigendum/eligibility/outcome diffs
  bid_analysis/          relevance scoring, GeM document parser, issue detection
notifications/           email templates, senders (console/Resend/SMTP), queue
database/                models, Alembic migrations, schema.sql, seed (categories.yaml)
infrastructure/gcp/      deploy.sh and verify.sh for Google Cloud
tests/                   pytest suite with synthetic portal + PDF fixtures
docs/                    source coverage, matching rules, operations runbook
```

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

```bash
cp .env.example .env.local   # then set ADMIN_PASSWORD_HASH (python3 -m core.passwords) and SESSION_SECRET
```

```bash
scripts/dev.sh migrate
```

```bash
scripts/dev.sh scan     # one manual scan (polite: 3 s between requests)
```

```bash
scripts/dev.sh serve    # http://localhost:8080
```

```bash
.venv/bin/python -m pytest -q
```

A local scan is only for development. Production scanning runs in the cloud (see [DEPLOYMENT.md](DEPLOYMENT.md)).

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): components, data flow, reliability design, technical decisions
- [DEPLOYMENT_FREE.md](DEPLOYMENT_FREE.md): zero-cost deployment (GitHub Actions + Neon + Render + Resend)
- [DEPLOYMENT.md](DEPLOYMENT.md): Google Cloud deployment (paid account required)
- [CONFIGURATION.md](CONFIGURATION.md): every environment variable and dashboard setting
- [SECURITY.md](SECURITY.md): secrets, auth, data protection, portal etiquette
- [COST_ESTIMATE.md](COST_ESTIMATE.md): expected monthly cost and free-tier limits
- [docs/SOURCE_COVERAGE.md](docs/SOURCE_COVERAGE.md): what is and isn't monitored, and why
- [docs/MATCHING_RULES.md](docs/MATCHING_RULES.md): how relevance scores are computed
- [docs/RUNBOOK.md](docs/RUNBOOK.md): day-to-day operations and troubleshooting

## Known limitations (honest list)

- **GeM is the only source scanned in practice.** CPPP (eprocure.gov.in), state NIC portals and IREPS need CAPTCHA
  or login, and BidRadar does not bypass either.
- **GeM is reachable only from Indian IP addresses** (verified 19 Nov 2026), so the scanner must run in India.
- **The live GeM check could not be repeated from this development machine.** On 18 Nov 2026 the machine could not
  connect to `gem.gov.in` or `bidplus.gem.gov.in`, while other government sites responded. This is most likely
  throttling after earlier research traffic. All GeM behaviour is covered by synthetic tests built from the
  responses recorded in Phase 1/2. The first cloud scan must confirm GeM is reachable from Google Cloud
  (DEPLOYMENT.md, step 7).
- GeM's search index can lag up to about 15 minutes (per GeM's own banner), so "every 30 minutes" means new bids
  are usually seen within 30–45 minutes.
- Scanned (image-only) PDFs are reported as "OCR unavailable" unless OCR is installed (off by default to keep the
  image small).
- PIN-to-state mapping uses India Post circles and is approximate.
