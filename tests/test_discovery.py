"""Requirements 1, 5, 8, 9, 13: discovery, duplicates, updates, corrigenda/cancellation/outcome, source recovery.
Runs the real pipeline + real GeM adapter against an in-memory mock of the portal."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from core.timeutil import IST, utcnow
from database.schema.models import (EmailNotification, ScanError, ScanExecution, Source, Tender, TenderDocument,
                                    TenderEvent)
from database.session import session_scope
from tests.fixtures.synthetic import corrigendum_html, gem_bid_pdf, gem_doc, result_html


def ist_z(days: float) -> str:
    """GeM listing format: IST wall-clock with a 'Z' suffix."""
    return (utcnow() + timedelta(days=days)).astimezone(IST).strftime("%Y-%m-%dT%H:%M:00Z")


def tenders(db):
    return db.execute(select(Tender).order_by(Tender.id)).scalars().all()


def events(db, kind=None):
    q = select(TenderEvent)
    if kind:
        q = q.where(TenderEvent.event_type == kind)
    return db.execute(q).scalars().all()


def notifications(db, kind=None):
    q = select(EmailNotification)
    if kind:
        q = q.where(EmailNotification.event_type == kind)
    return db.execute(q).scalars().all()


# --- 1. new tender discovery ----------------------------------------------------------------------------
def test_new_relevant_tender_is_discovered_scored_and_alert_held(gem, run_scan):
    gem.docs = [gem_doc(7000001, "Repair and Overhauling Service - DG Set", end=ist_z(12)),
                gem_doc(7000002, "Hiring of bus services for staff", end=ist_z(12))]
    gem.pdfs["7000001"] = gem_bid_pdf()
    res = run_scan({"gem_bidplus": gem.adapter()})
    assert res["status"] == "succeeded", res
    with session_scope() as db:
        rel = db.execute(select(Tender).where(Tender.external_id == "7000001")).scalar_one()
        assert rel.relevance_score >= 75 and rel.best_category == "F" and rel.is_relevant
        assert rel.bid_number == "GEM/2026/B/7000001"
        assert rel.estimated_value == 850000 and rel.emd == 17000      # from the tender document
        assert rel.state == "Delhi" or rel.state is None
        assert "buyer-login-123" not in str(rel.details)              # buyer login id never stored
        doc = db.execute(select(TenderDocument).where(TenderDocument.tender_id == rel.id)).scalar_one()
        assert doc.status == "extracted" and "<email-redacted>" in doc.text_excerpt
        assert rel.details["eligibility"] and rel.details["requirements"]
        n = notifications(db, "new_tender")
        assert len(n) == 1 and n[0].status == "held_no_recipient" and n[0].recipient is None
        irr = db.execute(select(Tender).where(Tender.external_id == "7000002")).scalar_one_or_none()
        assert irr is None or irr.relevance_score < 50


# --- 5. duplicate detection -----------------------------------------------------------------------------
def test_repeated_scans_and_overlapping_search_terms_create_no_duplicates(gem, run_scan):
    # title matches several discovery terms ('dg set', 'genset', 'repair and overhauling') -> returned many times
    gem.docs = [gem_doc(7000010, "Repair and overhauling of DG set genset diesel generator", end=ist_z(10))]
    run_scan({"gem_bidplus": gem.adapter()})
    run_scan({"gem_bidplus": gem.adapter()})
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Tender)) == 1
        assert len(notifications(db, "new_tender")) == 1
        assert len(events(db, "discovered")) == 1


def test_reverse_auction_merges_into_parent_bid(gem, run_scan):
    gem.docs = [gem_doc(7000020, "Repair of cylinder head of trucks", end=ist_z(5))]
    run_scan({"gem_bidplus": gem.adapter()})
    gem.docs = [gem_doc(7100020, "Repair of cylinder head of trucks", end=ist_z(7), bid_type=2,
                        number="GEM/2026/R/7100020", parent_id=7000020, parent_number="GEM/2026/B/7000020")]
    run_scan({"gem_bidplus": gem.adapter()})
    with session_scope() as db:
        ts = tenders(db)
        assert len(ts) == 1
        assert ts[0].ra_number == "GEM/2026/R/7100020" and ts[0].bid_number == "GEM/2026/B/7000020"
        assert len(events(db, "reverse_auction_started")) == 1
        assert not notifications(db, "deadline_changed")       # RA timeline is not a deadline change


# --- 8. tender updates ----------------------------------------------------------------------------------
def test_deadline_extension_detected_and_alerted_once(gem, run_scan):
    gem.docs = [gem_doc(7000030, "Repair of cars and light motor vehicles", end=ist_z(6))]
    run_scan({"gem_bidplus": gem.adapter()})
    gem.docs = [gem_doc(7000030, "Repair of cars and light motor vehicles", end=ist_z(9))]
    run_scan({"gem_bidplus": gem.adapter()})
    run_scan({"gem_bidplus": gem.adapter()})                # same state again: nothing new
    with session_scope() as db:
        ev = events(db, "deadline_changed")
        assert len(ev) == 1 and ev[0].significant and ev[0].old_value != ev[0].new_value
        assert len(notifications(db, "deadline_changed")) == 1
        assert "Deadline changed from" in notifications(db, "deadline_changed")[0].body_text


def test_cosmetic_text_change_is_not_significant(gem, run_scan):
    end = ist_z(6)
    gem.docs = [gem_doc(7000040, "Repair of Cars", end=end)]
    run_scan({"gem_bidplus": gem.adapter()})
    gem.docs = [gem_doc(7000040, "Repair  of  cars.", end=end)]
    run_scan({"gem_bidplus": gem.adapter()})
    with session_scope() as db:
        assert not [e for e in events(db) if e.significant and e.event_type != "discovered"]


# --- 9. corrigendum, cancellation and outcome detection --------------------------------------------------
def _force_refresh():
    with session_scope() as db:
        for t in tenders(db):
            t.last_refreshed_at = None


def test_corrigendum_detected_on_refresh(gem, run_scan):
    gem.docs = [gem_doc(7000050, "Repair of fuel injection pump FIP", end=ist_z(8))]
    run_scan({"gem_bidplus": gem.adapter()})
    gem.corrigenda["7000050"] = corrigendum_html("20-11-2026 10:00:00|Bid extended to 30-11-2026 15:00:00")
    _force_refresh()
    run_scan({"gem_bidplus": gem.adapter()})
    with session_scope() as db:
        t = tenders(db)[0]
        assert t.details["corrigenda_count"] == 1
        assert len(events(db, "corrigendum_published")) == 1
        n = notifications(db, "corrigendum")
        assert len(n) == 1 and "Bid extended to 30-11-2026 15:00:00" in n[0].body_text
    _force_refresh()
    run_scan({"gem_bidplus": gem.adapter()})                # same corrigendum again -> no new event
    with session_scope() as db:
        assert len(events(db, "corrigendum_published")) == 1


def test_cancellation_detected(gem, run_scan):
    d = gem_doc(7000060, "Repair of cylinder head assembly", end=ist_z(8))
    gem.docs = [d]
    run_scan({"gem_bidplus": gem.adapter()})
    gem.docs, gem.cancelled = [], [dict(d, b_status=[3])]
    _force_refresh()
    run_scan({"gem_bidplus": gem.adapter()})
    with session_scope() as db:
        assert tenders(db)[0].status == "cancelled"
        assert len(events(db, "cancelled")) == 1 and len(notifications(db, "cancelled")) == 1


def test_outcome_detected_without_naming_losing_bidders(gem, run_scan):
    d = gem_doc(7000070, "Reboring of engine block", end=ist_z(3))
    gem.docs = [d]
    run_scan({"gem_bidplus": gem.adapter()})
    # bid moves to financial evaluation; result page published (names masked during evaluation)
    gem.docs = [dict(d, b_buyer_status=[2])]
    gem.results["7000070"] = result_html([92000.0, 88000.0, 99500.0], masked=True)
    _force_refresh()
    run_scan({"gem_bidplus": gem.adapter()})
    with session_scope() as db:
        t = tenders(db)[0]
        out = t.details.get("outcome")
        assert out and out["bidders"] == 3 and out["ranks"][0] == ["L1", 88000.0]
        assert out["l1_name"] is None                      # not awarded yet -> winner not published here
        assert "Seller 2" not in str(t.details)
        assert len(events(db, "outcome_available")) == 1


# --- 13. failed source recovery -------------------------------------------------------------------------
def test_failed_source_is_recorded_and_recovers(gem, run_scan):
    gem.docs = [gem_doc(7000080, "Repair of DG set", end=ist_z(8))]
    gem.fail_listing = True
    res = run_scan({"gem_bidplus": gem.adapter()})
    assert res["status"] == "failed"
    with session_scope() as db:
        src = db.execute(select(Source).where(Source.key == "gem_bidplus")).scalar_one()
        assert src.consecutive_failures == 1 and src.last_error
        assert db.scalar(select(func.count()).select_from(ScanError)) >= 1
        assert db.execute(select(ScanExecution)).scalars().first().status == "failed"
    gem.fail_listing = False
    res = run_scan({"gem_bidplus": gem.adapter()})
    assert res["status"] in ("succeeded", "partial")       # 'partial' = e.g. a tender PDF was unavailable
    with session_scope() as db:
        src = db.execute(select(Source).where(Source.key == "gem_bidplus")).scalar_one()
        assert src.consecutive_failures == 0 and src.last_success_at is not None
        assert db.scalar(select(func.count()).select_from(Tender)) == 1


def test_one_bad_document_does_not_fail_the_scan(gem, run_scan):
    gem.docs = [gem_doc(7000090, "Repair of DG set", end=ist_z(8))]
    gem.pdfs["7000090"] = b"<html>temporarily unavailable</html>"
    res = run_scan({"gem_bidplus": gem.adapter()})
    assert res["status"] in ("succeeded", "partial")
    with session_scope() as db:
        doc = db.execute(select(TenderDocument)).scalar_one()
        assert doc.status == "failed"
        t = tenders(db)[0]
        assert any("not analysed" in i for i in t.details["issues"])

def test_source_with_no_successful_page_is_not_reported_healthy(gem, run_scan):
    """Regression (found in the live run on 18 Nov 2026): a listing-only source whose single query timed out was
    reported 'ok' and the scan 'partial' although nothing had been fetched."""
    from scanner.source_adapters.base import SourceAdapter

    class Down(SourceAdapter):
        key = "cppp_gem_mirror"

        def queries(self, terms):
            return ["all"]

        def fetch_page(self, q, page):
            raise TimeoutError("connect timed out")

    gem.docs = [gem_doc(7000100, "Repair of DG set", end=ist_z(8))]
    res = run_scan({"gem_bidplus": gem.adapter(), "cppp_gem_mirror": Down()})
    assert res["status"] == "partial" and res["stats"]["sources"]["cppp_gem_mirror"]["status"] == "failed"
    res = run_scan({"cppp_gem_mirror": Down()})
    assert res["status"] == "failed"
    with session_scope() as db:
        src = db.execute(select(Source).where(Source.key == "cppp_gem_mirror")).scalar_one()
        assert src.last_success_at is None and src.consecutive_failures == 2


def test_missing_documents_do_not_trip_the_circuit_breaker(gem, run_scan):
    """Regression (found in the demo run): three 404 PDFs opened the source's circuit breaker and blocked every
    other document and refresh in the scan."""
    gem.docs = [gem_doc(7000200 + i, f"Repair of DG set unit {i}", end=ist_z(8)) for i in range(5)]
    gem.pdfs["7000204"] = gem_bid_pdf(number="GEM/2026/B/7000204")       # only the last one has a PDF
    run_scan({"gem_bidplus": gem.adapter()})
    with session_scope() as db:
        docs = {d.url.rsplit("/", 1)[1]: d.status for d in db.execute(select(TenderDocument)).scalars()}
        assert docs["7000204"] == "extracted"
        assert not db.execute(select(ScanError).where(ScanError.message.contains("circuit open"))).scalars().all()
        assert db.execute(select(Tender).where(Tender.external_id == "7000204")).scalar_one().last_refreshed_at


def test_source_outage_defers_documents_instead_of_failing_them(gem, run_scan):
    gem.docs = [gem_doc(7000300, "Repair of DG set", end=ist_z(8))]
    gem.pdfs["7000300"] = gem_bid_pdf(number="GEM/2026/B/7000300")
    run_scan({"gem_bidplus": gem.adapter()})
    gem.fail_listing = True
    run_scan({"gem_bidplus": gem.adapter()})
    with session_scope() as db:
        errs = db.execute(select(ScanError).where(ScanError.stage == "refresh")).scalars().all()
        assert len(errs) <= 1                                           # one deferral note, not one per tender
