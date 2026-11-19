# How relevance is scored

Every listing is scored from 0 to 100 against each enabled category (A–F). The best category's score is the
tender's score. The code is in `scanner/bid_analysis/relevance.py`; the keywords are in
`database/seed/categories.yaml` and editable in Settings → Keywords.

## 1. Normalisation (`keyword_engine/text.py`)

- Text is lower-cased and abbreviations are repaired:
  - D.G. set → dg set, C.R.D.I → crdi, OBD-II → obd2, F.I.P → fip;
  - gen-set → genset, re-boring → reboring, crank shaft → crankshaft;
  - annual/comprehensive maintenance contract → amc/cmc.
- Words are then lightly stemmed, so servicing/serviced/service all match.

## 2. Signals (points add up)

| Signal | Points | Example |
|---|---|---|
| Exact phrase (from the category's phrase list) | 55, +8 per extra phrase (max +16) | "repair and overhauling of dg set" |
| Synonym / related term | 22, +6 per extra (max +12) | "fuel injection pump", "dg set" |
| Concept: an **object** near an **action** | 58 × strength; +17 if within 4 meaningful words | "cylinder head" … "repair"; "injector" … "reconditioning" |
| Fuzzy spelling (only when no phrase matched) | 15 | partial match ≥ 90% |
| Two or more of phrase / synonym / concept | +10 | |
| Tender-document evidence (PDF) | up to 25 | phrase 15, synonym 8, concept 12 × strength |
| Optional embedding similarity | up to 15 | off by default |

**Concept strength** is proximity × action weight × object weight:
- Proximity is 1.0 within 4 words, 0.75 within 10, 0.45 within 25, otherwise 0.2. Numbers, units ("125 kva") and
  stop-words are not counted as distance.
- Generic actions (servicing, maintenance, testing, AMC, CMC, rectification) carry weight 0.85.
- Generic objects can carry a lower weight. For example, "valve" carries 0.55, so "repair of compressor valves" is
  not cylinder-head work.

**Spelling correction.** Words of 6+ letters that are within 88% of a category word are corrected
("injecton" → "injection") and the text is re-scored. The corrected score is used only if it is clearly better,
minus 5 points, and the explanation names the correction.

## 3. Penalties and caps

| Rule | Effect |
|---|---|
| Exclusion term present (e.g. "document scanner", "barcode", "hiring of vehicles", "bore well", "spices") | −45 |
| Supply-only purchase (product bid with no repair or service action; not applied to D) | −25 |
| Equipment purchase ("grinding machine", "diagnostic kit", …) with no job-work words | capped at 35 |
| No signal in the listing title/description | capped at 45 (55 with document evidence) |

## 4. Bands and thresholds

| Score | Band | Default behaviour |
|---|---|---|
| 90–100 | very strong | alert |
| 75–89 | strong | alert (default threshold 75) |
| 50–74 | potentially relevant | stored, flagged relevant, shown in Explorer; PDF analysed (≥ 40) |
| 25–49 | weak | stored; PDF analysed if ≥ 40 |
| 0–24 | irrelevant | stored for audit only |

The threshold is set in Settings → Matching.

## 5. Calibration (all covered by `tests/test_matching.py`)

| Text | Score |
|---|---|
| Repair and overhauling of DG set 125 KVA | 100 (F) |
| Annual maintenance contract for 125 KVA diesel generator sets | 98 (F) |
| Reconditioning of common rail fuel injectors of Ashok Leyland trucks | 100 (A) |
| Repair of Cylinder Head of TATA 407 vehicle | 75 (B) |
| Repair of fuel injecton pump *(misspelt)* | 95 (A) |
| Hiring of DG set on monthly basis | 22 |
| Surface grinding machine with magnetic chuck | 35 (capped) |
| Supply of new diesel generator set 500 KVA | 0 |
| Document scanner / bus hiring / ELISA reader / spices / bore well / desktop PCs | 0 |
| Repair of AC, computers, lifts, furniture, submersible pumps | 0 |

If you see a false positive or a miss, either add an exclusion or phrase in Settings → Keywords (stored tenders are
re-scored immediately), or add the case to `tests/test_matching.py` and adjust the seed.
