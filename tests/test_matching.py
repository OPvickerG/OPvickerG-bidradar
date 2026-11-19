"""Requirements 2–4: keyword matching, semantic matching, irrelevant-tender rejection."""
from __future__ import annotations

import pytest

from scanner.bid_analysis.relevance import band, score_tender
from scanner.keyword_engine.text import normalise, stem_phrase


# --- 2. keyword matching -------------------------------------------------------------------------------
@pytest.mark.parametrize("title,cat", [
    ("Repair of Cylinder Head of TATA 407 vehicle", "B"),
    ("Reboring of engine block and crankshaft grinding", "C"),
    ("CRDI injector repair and calibration", "A"),
    ("Repair and overhauling of cars", "F"),
    ("OBD diagnostic scanning of vehicles", "D"),
    ("Surface grinding of pressure plates and flywheel facing", "E"),
])
def test_exact_phrase_matches_expected_category(title, cat):
    r = score_tender(title, procurement_type="service")
    assert r.best_category == cat
    assert r.score >= 75, r.explanation
    assert r.explanation and r.explanation.lower() != "no business category matched."


def test_normalisation_handles_abbreviations_and_stems():
    assert "dg set" in normalise("Repair of D.G. Set 125 KVA")
    assert "crdi" in normalise("C.R.D.I pump")
    assert stem_phrase("servicing") == stem_phrase("service")
    assert score_tender("Servicing of D.G. Sets", procurement_type="service").best_category == \
        score_tender("Service of DG set", procurement_type="service").best_category == "F"


def test_fuzzy_match_tolerates_misspelling():
    r = score_tender("Cylinder head reconditionng and valve seat grindng", procurement_type="service")
    assert r.best_category == "B" and r.score >= 75 and "spelling" in r.explanation


# --- 3. semantic matching (concept proximity, no exact phrase) -------------------------------------------
@pytest.mark.parametrize("title,cat,minimum", [
    ("Reconditioning of common rail fuel injectors of Ashok Leyland trucks", "A", 75),
    ("Annual maintenance contract for 125 KVA diesel generator sets", "F", 75),
    ("Overhauling of engine of Mahindra Bolero including crankshaft and block machining", "C", 75),
])
def test_semantic_concepts_without_seed_phrase(title, cat, minimum):
    r = score_tender(title, procurement_type="service")
    assert r.best_category == cat, r.explanation
    assert r.score >= minimum, r.explanation
    assert "concept" in r.explanation or "related term" in r.explanation or "phrase" in r.explanation


def test_document_evidence_raises_weak_listing():
    weak = score_tender("Hiring of services - as per scope", procurement_type="service")
    strong = score_tender("Hiring of services - as per scope", procurement_type="service",
                          document_text="Scope of work: repair and overhauling of DG set 62.5 KVA including "
                                        "decarbonising and replacement of fuel injection pump.")
    assert strong.score > weak.score
    assert strong.score <= 55          # document-only evidence is capped (needs confirmation in the listing)


# --- 4. irrelevant tender rejection ---------------------------------------------------------------------
@pytest.mark.parametrize("title", [
    "Document scanner for office use",
    "Barcode scanner handheld",
    "Hiring of bus for staff transportation",
    "Supply of new diesel generator set 500 KVA",
    "ELISA reader for pathology lab",
    "Turmeric and chilli spices powder",
    "Bore well drilling and boring of tube well",
    "Desktop computers all in one",
    "Monthly basis cab and taxi hiring services",
])
def test_irrelevant_tenders_score_low(title):
    r = score_tender(title, procurement_type="product" if "supply" in title.lower() else None)
    assert r.score < 50, f"{title!r} scored {r.score}: {r.explanation}"


def test_equipment_purchase_is_capped():
    r = score_tender("Surface grinding machine with magnetic chuck", procurement_type="product")
    assert r.score <= 35


def test_bands():
    assert [band(s) for s in (95, 80, 60, 30, 10)] == ["very strong", "strong", "potentially relevant", "weak", "irrelevant"]


# --- calibration table in docs/MATCHING_RULES.md --------------------------------------------------------
@pytest.mark.parametrize("title,ptype,low,high,cat", [
    ("Repair and overhauling of DG set 125 KVA", "service", 90, 100, "F"),
    ("Annual maintenance contract for 125 KVA diesel generator sets", "service", 90, 100, "F"),
    ("Reconditioning of common rail fuel injectors of Ashok Leyland trucks", "service", 90, 100, "A"),
    ("Repair of Cylinder Head of TATA 407 vehicle", "service", 75, 100, "B"),
    ("Repair of fuel injecton pump", "service", 75, 100, "A"),
    ("Hiring of DG set on monthly basis", "service", 0, 24, None),
    ("Surface grinding machine with magnetic chuck", "product", 0, 35, None),
    ("Supply of new diesel generator set 500 KVA", "product", 0, 0, None),
    ("Repair of AC and refrigerators", "service", 0, 0, None),
    ("Repair of computer and printers", "service", 0, 0, None),
    ("Maintenance of lifts", "service", 0, 0, None),
    ("Repair of office furniture", "service", 0, 0, None),
    ("Repair of submersible pump", "service", 0, 0, None),
    ("Repair of valves of compressor", "service", 0, 49, None),
])
def test_documented_calibration(title, ptype, low, high, cat):
    r = score_tender(title, procurement_type=ptype)
    assert low <= r.score <= high, (r.score, r.explanation)
    if cat:
        assert r.best_category == cat


def test_phrases_do_not_span_title_and_description():
    """Regression: 'TATA 407 vehicle' + description 'Repair of ...' must not create the phrase 'vehicle repair'."""
    r = score_tender("Repair of Cylinder Head of TATA 407 vehicle", "Repair of Cylinder Head of TATA 407 vehicle",
                     procurement_type="service")
    assert r.best_category == "B"
    f = next(c for c in r.categories if c.code == "F")
    assert "vehicle repair" not in f.matched_phrases


# --- regressions found on the first live GeM scan (19 Nov 2026) -------------------------------------------
@pytest.mark.parametrize("title,desc", [
    ("B VEH SPARES", "ARMATURE ASSY,KIT STARTER MOTOR,PUMP FUEL INJECTION ROTARY,INJECTOR,REPAIR KIT MAJOR SLAVE CYLINDER"),
    ("Rectifier Assy,Field Regulator,Air Hose Rubber,Major Repair Kit,Liner Assy,Rocker Head Assy", ""),
    ("Procurement of Tools and Equipments for ITI", "PORTABLE WELDING MACHINE,Argon Cylinder 25 Liter,Metal cutoff Machine"),
    ("Electric Lift", "Supply fire in repair push button box complete with stainless steel face plate 1.5mm thick"),
    ("Automobile Items", "Cut section of 4 or 6 cylinder diesel engine with moving condition,Spanner set"),
])
def test_spares_lists_and_cross_item_pairs_do_not_alert(title, desc):
    r = score_tender(title, desc, procurement_type="product")
    assert r.score < 50, (r.score, r.explanation)


@pytest.mark.parametrize("title,desc", [
    ("REWINDING OF STATOR 15 KVA", "REWINDING OF STATOR 15 KVA,HEAD GRINDING,REPAIR OF FUEL PUMP 15 KVA,STARTER MOTOR REPAIR"),
    ("Repair to clutch system including by replace of 01 Set Clutch Plate", "Repair to engine heating system by replace of 01 No Water pump"),
])
def test_real_repair_jobs_filed_as_product_bids_still_alert(title, desc):
    assert score_tender(title, desc, procurement_type="product").score >= 75
