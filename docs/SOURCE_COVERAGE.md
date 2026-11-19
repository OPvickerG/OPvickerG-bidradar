# Source coverage

BidRadar claims coverage **only** for the sources marked *scanned* below. The same list, with live health, is shown
under Settings → Sources and in the Monitoring Centre.

| Source | Status | What is read | Why / limitations |
|---|---|---|---|
| **GeM BidPlus** (bidplus.gem.gov.in) | **scanned** (enabled) | Public bid listing (search by 30 category terms), bid / RA PDFs, corrigendum flag + list, public result page | Main source for service jobs of this type. GeM's index can lag about 15 min. Throttled to 1 request per 3 s. **Note:** on 18 Nov 2026 this development machine could not connect to any gem.gov.in host (other government sites worked), most likely throttling after earlier research traffic. Reachability from Google Cloud must be confirmed on the first cloud scan. |
| CPPP central tenders via GeM mirror (gem.gov.in/cppp) | scanned, **unreliable** | Listing rows only (title, organisation, dates, tender id) | Mirror's title filter does not work, so newest pages are scanned and matched locally. Detail pages on CPPP need a CAPTCHA, so they are not opened. Mostly civil works. |
| CPPP state tenders via GeM mirror (gem.gov.in/cppp_state) | scanned, **unreliable** | Same as above | Same as above |
| CPPP eProcurement (eprocure.gov.in) | **requires CAPTCHA**, not scanned | — | Listing and search pages show a CAPTCHA (checked Nov 2026). Not bypassed. |
| State NIC GePNIC portals (etenders.gov.in and state instances) | **requires CAPTCHA**, not scanned | — | Same platform as CPPP. Partly visible through the CPPP state mirror. |
| IREPS (Indian Railways) | **requires login**, not scanned | — | Search and participation need registration. |
| GeM BidNext (railway bids) | not implemented (unreliable) | — | Session-bound widget rendering; no stable public endpoint found. |
| Defence / CPSE own portals | not integrated | — | Many of these buyers also use GeM, which is covered. |

## Adding a source

1. Implement `SourceAdapter` in `scanner/source_adapters/` (see `base.py`):
   - `queries()` returns the search terms;
   - `fetch_page()` returns a newest-first list of `RawTender`, empty when done;
   - `refresh()` re-checks one tender;
   - `fetch_bytes()` downloads a document.
2. Use `PoliteClient` for all HTTP calls (throttle, retries, circuit breaker).
3. Register it in `registry.py`. It is seeded into `sources` on the next scan.
4. Add a mock-transport test like `tests/test_discovery.py`.

Never add an adapter that solves CAPTCHAs, reuses someone's login, or ignores a portal's access restrictions.
