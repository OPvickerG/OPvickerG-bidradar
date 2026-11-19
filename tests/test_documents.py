"""Requirements 6–7: deadline extraction and document extraction."""
from __future__ import annotations

from datetime import datetime, timezone

from core.timeutil import fmt_ist, parse_gem_listing_ts, parse_ist_string, slot_for
from scanner.bid_analysis.gem_document import parse_gem_bid_text
from scanner.document_processing.extract import extract_pdf, redact
from scanner.source_adapters.gem_bidplus import doc_to_raw
from tests.fixtures.synthetic import gem_bid_pdf, gem_doc, make_pdf


# --- 6. deadline extraction -----------------------------------------------------------------------------
def test_gem_listing_timestamp_is_ist_wallclock():
    # GeM's listing shows '...T15:00:00Z' but the value is IST wall-clock time, not UTC.
    dt = parse_gem_listing_ts("2026-12-05T15:00:00Z")
    assert dt == datetime(2026, 12, 5, 9, 30, tzinfo=timezone.utc)
    assert fmt_ist(dt) == "05 Dec 2026, 15:00 IST"


def test_document_deadline_formats():
    assert parse_ist_string("05-12-2026 15:00:00") == datetime(2026, 12, 5, 9, 30, tzinfo=timezone.utc)
    assert parse_ist_string("not a date") is None
    assert parse_ist_string(None) is None


def test_listing_record_to_tender_deadline():
    raw = doc_to_raw(gem_doc(7000001, "Repair of DG set", end="2026-12-05T15:00:00Z"))
    assert raw.deadline_at == datetime(2026, 12, 5, 9, 30, tzinfo=timezone.utc)
    assert raw.status == "active"


def test_slot_alignment_ist():
    t = datetime(2026, 11, 18, 4, 44, 10, tzinfo=timezone.utc)      # 10:14 IST
    assert fmt_ist(slot_for(t, 30)) == "18 Nov 2026, 10:00 IST"


# --- 7. document extraction -----------------------------------------------------------------------------
def test_pdf_text_extraction_and_fields():
    res = extract_pdf(gem_bid_pdf())
    assert res.status == "extracted" and res.pages == 1 and len(res.sha256) == 64
    f = parse_gem_bid_text(res.text)
    assert f["bid_number"] == "GEM/2026/B/7000001"
    assert f["bid_end"] == "05-12-2026 15:00:00"
    assert f["estimated_value"] == 850000
    assert f["emd_amount"] == 17000
    assert f["min_avg_turnover_inr"] == 1_000_000
    assert f["epbg_percent"] == 3.0
    assert f["contract_period"] == "1 Year(s)"
    assert f["organisation"] == "Indian Army"
    assert f["years_experience"] == "3 Year"
    assert "110010" in f["consignee_pins"]


def test_personal_data_redacted_from_document_text():
    res = extract_pdf(gem_bid_pdf())
    assert "officer.name@example.gov.in" not in res.text and "9876543210" not in res.text
    assert "<email-redacted>" in res.text and "<phone-redacted>" in res.text
    assert redact("call 9123456789") == "call <phone-redacted>"


def test_missing_fields_are_null_not_invented():
    res = extract_pdf(make_pdf(["Bid Number: GEM/2026/B/1", "Some text without values for anything else at all"]))
    f = parse_gem_bid_text(res.text)
    assert f["estimated_value"] is None and f["emd_amount"] is None and f["contract_period"] is None


def test_bad_documents_are_classified():
    assert extract_pdf(b"<html>error</html>").status == "failed"
    assert extract_pdf(make_pdf([])).status in ("ocr_unavailable", "no_text_layer")
    assert extract_pdf(b"%PDF-1.4 garbage").status == "failed"
