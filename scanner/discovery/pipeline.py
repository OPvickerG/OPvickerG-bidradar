"""One scan: discover -> upsert & detect changes -> analyse documents -> refresh tracked tenders ->
re-score -> queue notifications -> send queue -> summaries.

Everything is idempotent: running the same scan twice creates no duplicate tenders, matches, documents,
events or notifications (unique constraints + dedupe keys)."""
from __future__ import annotations

import logging
import time
import traceback
from datetime import timedelta
from typing import Callable, Dict, List, Optional, Set

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend import settings_service as cfg
from core.config import get_settings
from core.geo import state_from_pin, state_from_text
from core.logs import log
from core.timeutil import IST, ensure_utc, fmt_ist, parse_ist_string, to_ist, utcnow
from database.schema.models import (BidObservation, EmailNotification, ScanError, ScanExecution, Source, Tender,
                                    TenderDocument, TenderEvent, TenderMatch)
from database.seed.loader import load_models
from notifications.notification_queue import queue as nq
from scanner.bid_analysis.gem_document import eligibility_summary, parse_gem_bid_text, requirements_summary
from scanner.bid_analysis.issues import missing_information, potential_issues
from scanner.bid_analysis.relevance import score_tender
from scanner.change_detection.detector import (EVENT_TO_PREF, Change, diff_corrigenda, diff_eligibility,
                                               diff_listing, diff_outcome, state_fingerprint)
from scanner.document_processing.extract import extract_pdf
from scanner.source_adapters.base import RawTender, SourceAdapter, SourceUnavailable
from scanner.source_adapters.registry import adapter_classes

logger = logging.getLogger("bidradar.scan")
DOC_ANALYSIS_MIN_SCORE = 40
RELEVANT_MIN_SCORE = 50          # stored flag used by the dashboard; alerts use the user's threshold
DOC_TEXT_KEEP = 20000            # redacted English text kept for re-scoring (never personal data)


class ScanBudgetExceeded(Exception):
    pass


class Scan:
    def __init__(self, db: Session, execution: ScanExecution, adapters: Optional[Dict[str, SourceAdapter]] = None,
                 sender=None, catch_up: bool = False, time_budget_s: Optional[float] = None):
        self.db, self.ex, self.sender = db, execution, sender
        self.s = get_settings()
        self.injected = adapters
        self.adapters: Dict[str, SourceAdapter] = {}
        self.max_pages = self.s.backfill_pages_per_term if catch_up else self.s.max_pages_per_term
        self.deadline = time.monotonic() + (time_budget_s or (self.s.scan_lease_minutes - 4) * 60)
        self.stats: Dict = {"sources": {}, "listings_seen": 0, "new_tenders": 0, "updated_tenders": 0,
                            "relevant_new": 0, "documents_analysed": 0, "refreshed": 0, "events": 0,
                            "notifications_queued": 0, "errors": 0, "requests": 0}
        self.seen: Set[tuple] = set()
        self.down: Set[str] = set()                # sources whose circuit breaker opened during this scan
        self.touched: Dict[int, Tender] = {}
        self.pending_changes: List[tuple] = []     # (tender, Change)
        self.models = []
        self.embedder = None

    # ------------------------------------------------------------------ helpers
    def _time_left(self) -> bool:
        return time.monotonic() < self.deadline

    def error(self, stage: str, msg: str, source_key: Optional[str] = None, tender_ref: Optional[str] = None,
              detail: Optional[str] = None):
        self.stats["errors"] += 1
        self.db.add(ScanError(scan_id=self.ex.id, source_key=source_key, stage=stage, message=msg[:2000],
                              detail=(detail or "")[:4000] or None, tender_ref=tender_ref))
        log(logger, logging.WARNING, "scan error", stage=stage, source=source_key, tender=tender_ref, error=msg[:300])

    def event(self, t: Tender, c: Change):
        self.db.add(TenderEvent(tender_id=t.id, scan_id=self.ex.id, event_type=c.event_type, field=c.field,
                                old_value=c.old, new_value=c.new, significant=c.significant))
        self.stats["events"] += 1
        if c.significant and c.event_type in EVENT_TO_PREF:
            self.pending_changes.append((t, c))

    def adapter(self, key: str) -> Optional[SourceAdapter]:
        if key in self.adapters:
            return self.adapters[key]
        if self.injected is not None:
            a = self.injected.get(key)
        else:
            cls = adapter_classes().get(key)
            a = cls() if cls and getattr(cls, "implemented", True) else None
        if a is not None:
            self.adapters[key] = a
        return a

    # ------------------------------------------------------------------ run
    def run(self) -> Dict:
        db = self.db
        self.models = [m for m in load_models(db)]
        enabled_codes = set(cfg.get(db, "matching").get("enabled_categories") or [])
        for m in self.models:
            m.enabled = m.enabled and m.code in enabled_codes
        if self.s.embedding_backend == "fastembed":
            try:
                from scanner.semantic_matching.concepts import EmbeddingScorer
                self.embedder = EmbeddingScorer([m for m in self.models if m.enabled])
            except Exception as e:
                self.error("matching", f"embedding backend unavailable: {e}")
        terms = list(dict.fromkeys(t for m in self.models if m.enabled for t in m.discovery))
        sources = db.execute(select(Source).where(Source.enabled.is_(True)).order_by(Source.id)).scalars().all()
        try:
            for src in sources:
                self.discover(src, terms)
            self.analyse_documents()
            self.refresh_tracked()
            self.close_expired()
            self.notify()
        finally:
            for a in self.adapters.values():
                self.stats["requests"] += getattr(getattr(a, "http", None), "requests_made", 0) or 0
                try:
                    a.close()
                except Exception:
                    pass
        try:
            self.stats["email"] = nq.process_pending(db, sender=self.sender)
        except Exception as e:
            self.error("notify", f"queue processing failed: {e}", detail=traceback.format_exc())
        self.summaries()
        return self.stats

    # ------------------------------------------------------------------ discovery
    def discover(self, src: Source, terms: List[str]):
        a = self.adapter(src.key)
        st = self.stats["sources"].setdefault(src.key, {"pages": 0, "listings": 0, "new": 0, "changed": 0,
                                                         "status": "ok"})
        if a is None:
            st["status"] = "no_adapter"
            return
        failures, had_error = 0, False
        try:
            for q in a.queries(terms):
                for page in range(1, self.max_pages + 1):
                    if not self._time_left():
                        st["status"] = "time_budget_reached"
                        raise ScanBudgetExceeded()
                    try:
                        raws = a.fetch_page(q, page)
                    except (ScanBudgetExceeded, SourceUnavailable):
                        raise
                    except Exception as e:
                        failures += 1
                        had_error = True
                        self.error("discovery", f"{type(e).__name__}: {e}", src.key, detail=f"query={q!r} page={page}")
                        if failures >= self.s.source_failure_threshold:
                            raise RuntimeError(f"{failures} consecutive request failures; source skipped this scan")
                        break
                    failures = 0
                    st["pages"] += 1
                    if not raws:
                        break
                    fresh = 0
                    for raw in raws:
                        outcome = self.upsert(raw)
                        st["listings"] += 1
                        if outcome == "new":
                            st["new"] += 1
                            fresh += 1
                        elif outcome == "changed":
                            st["changed"] += 1
                            fresh += 1
                    self.db.flush()
                    if fresh == 0:        # newest-first ordering: nothing new on this page => stop paging
                        break
            if had_error and st["pages"] == 0:
                raise RuntimeError("no page could be fetched from this source")
            src.last_success_at, src.consecutive_failures = utcnow(), 0
            if src.status == "inaccessible":
                src.status = "enabled"
        except SourceUnavailable as e:
            self.down.add(src.key)
            st["status"] = "failed"
            src.consecutive_failures += 1
            src.last_error_at, src.last_error = utcnow(), str(e)[:1000]
            if src.consecutive_failures >= 6 and src.status == "enabled":
                src.status = "inaccessible"
            self.error("discovery", f"source failed: {e}", src.key)
        except ScanBudgetExceeded:
            if st["pages"] > 0:
                src.last_success_at = utcnow()
            else:
                st["status"] = "skipped_time_budget"
        except Exception as e:
            st["status"] = "failed"
            src.consecutive_failures += 1
            src.last_error_at, src.last_error = utcnow(), str(e)[:1000]
            if src.consecutive_failures >= 6 and src.status == "enabled":
                src.status = "inaccessible"
            self.error("discovery", f"source failed: {e}", src.key, detail=traceback.format_exc())
        src.updated_at = utcnow()

    @staticmethod
    def raw_state(raw: RawTender) -> Dict:
        return {"title": raw.title, "description": raw.description, "deadline_at": raw.deadline_at,
                "status": raw.status, "quantity": raw.quantity, "ra_number": raw.ra_number}

    @staticmethod
    def tender_state(t: Tender) -> Dict:
        return {"title": t.title, "description": t.description, "deadline_at": t.deadline_at, "status": t.status,
                "quantity": t.quantity, "estimated_value": t.estimated_value,
                "corrigenda_count": (t.details or {}).get("corrigenda_count", 0),
                "outcome_ranks": ((t.details or {}).get("outcome") or {}).get("ranks")}

    def upsert(self, raw: RawTender) -> str:
        key = (raw.source_key, raw.external_id)
        if key in self.seen:
            return "dup"
        self.seen.add(key)
        self.stats["listings_seen"] += 1
        db = self.db
        t = db.execute(select(Tender).where(Tender.source_key == raw.source_key,
                                            Tender.external_id == raw.external_id)).scalar_one_or_none()
        now = utcnow()
        if t is None:
            t = Tender(source_key=raw.source_key, external_id=raw.external_id, title=raw.title, details={},
                       first_seen_at=now, last_seen_at=now)
            self.apply_raw(t, raw)
            t.state = raw.state or state_from_text(raw.department, raw.organization, raw.ministry, raw.location)
            db.add(t)
            db.flush()
            self.score(t)
            self.event(t, Change("discovered", None, None, raw.bid_number or raw.external_id, False))
            self.observe(t)
            self.touched[t.id] = t
            self.stats["new_tenders"] += 1
            if t.relevance_score >= RELEVANT_MIN_SCORE:
                self.stats["relevant_new"] += 1
            return "new"
        old = self.tender_state(t)
        t.last_seen_at = now
        changes: List[Change] = []
        if raw.ra_number and raw.ra_number != t.ra_number:
            # a reverse auction was created from this bid: new timeline, not a "deadline change"
            changes.append(Change("reverse_auction_started", "ra_number", t.ra_number, raw.ra_number, False))
            new_state = dict(self.raw_state(raw), deadline_at=old["deadline_at"])
            changes += [c for c in diff_listing(old, new_state) if c.event_type != "deadline_changed"]
        elif raw.ra_number is None and t.ra_number:
            return "same"             # the parent bid listing is stale relative to its RA; keep the RA view
        else:
            changes = diff_listing(old, self.raw_state(raw))
        if not changes:
            return "same"
        self.apply_raw(t, raw)
        for c in changes:
            self.event(t, c)
        if any(c.event_type == "specification_changed" and c.significant for c in changes):
            self.score(t)
        self.observe(t)
        self.touched[t.id] = t
        self.stats["updated_tenders"] += 1
        return "changed"

    def apply_raw(self, t: Tender, raw: RawTender):
        for f in ("title", "bid_number", "ra_number", "parent_bid_number", "description", "procurement_type",
                  "ministry", "department", "organization", "published_at", "deadline_at", "opening_at", "status",
                  "stage", "quantity", "official_url", "document_url"):
            v = getattr(raw, f)
            if v is not None or f in ("status",):
                setattr(t, f, v)
        if raw.location:
            t.location = raw.location
        d = dict(t.details or {})
        d["listing"] = {k: v for k, v in (raw.extra or {}).items() if k != "flags"}
        d["flags"] = (raw.extra or {}).get("flags")
        docs = {u: k for k, u in raw.documents}
        d["document_urls"] = [{"kind": k, "url": u} for u, k in docs.items()]
        t.details = d
        t.last_verified_at = utcnow()

    def observe(self, t: Tender):
        st = self.tender_state(t)
        fp = state_fingerprint(st)
        if fp == t.fingerprint:
            return
        t.fingerprint = fp
        ser = {k: (fmt_ist(v) if hasattr(v, "tzinfo") else (float(v) if v is not None and not isinstance(v, (str, list, int)) else v))
               for k, v in st.items()}
        existing = self.db.execute(select(BidObservation).where(BidObservation.tender_id == t.id,
                                                                BidObservation.scan_id == self.ex.id)).scalar_one_or_none()
        if existing:
            existing.fingerprint, existing.state, existing.observed_at = fp, ser, utcnow()
        else:
            self.db.add(BidObservation(tender_id=t.id, scan_id=self.ex.id, fingerprint=fp, state=ser))

    # ------------------------------------------------------------------ scoring
    def score(self, t: Tender):
        doc_text = (t.details or {}).get("doc_text") or ""
        r = score_tender(t.title, t.description or "", doc_text, t.procurement_type, self.models, self.embedder)
        t.relevance_score, t.best_category, t.match_explanation = r.score, r.best_category, r.explanation
        t.is_relevant = r.score >= RELEVANT_MIN_SCORE
        existing = {m.category_code: m for m in t.matches}
        for c in r.categories:
            if c.score <= 0 and c.code not in existing:
                continue
            m = existing.get(c.code)
            if m is None:
                m = TenderMatch(tender_id=t.id, category_code=c.code, score=c.score)
                t.matches.append(m)
            m.score, m.matched_phrases, m.explanation, m.breakdown = c.score, c.matched_phrases, c.explanation, c.breakdown
            m.computed_at = utcnow()
        self.refresh_derived(t)

    def refresh_derived(self, t: Tender):
        d = dict(t.details or {})
        f = d.get("doc_fields") or {}
        d["issues"] = potential_issues(t.deadline_at, f, d.get("doc_status"))
        d["missing"] = missing_information({"estimated_value": t.estimated_value, "emd": t.emd or (f.get("emd_required") == "No"),
                                            "contract_duration": t.contract_duration, "location": t.location or t.state})
        t.details = d

    # ------------------------------------------------------------------ documents
    def analyse_documents(self):
        db = self.db
        budget = self.s.max_documents_per_scan
        cands = [t for t in self.touched.values() if t.relevance_score >= DOC_ANALYSIS_MIN_SCORE]
        cands += db.execute(select(Tender).where(Tender.relevance_score >= DOC_ANALYSIS_MIN_SCORE,
                                                 Tender.status == "active",
                                                 Tender.details.is_not(None))
                            .order_by(Tender.relevance_score.desc()).limit(200)).scalars().all()
        done: Set[int] = set()
        for t in sorted(cands, key=lambda x: -x.relevance_score):
            if budget <= 0 or not self._time_left():
                break
            if t.id in done:
                continue
            done.add(t.id)
            urls = (t.details or {}).get("document_urls") or []
            for du in urls[:2]:
                doc = db.execute(select(TenderDocument).where(TenderDocument.tender_id == t.id,
                                                              TenderDocument.url == du["url"])).scalar_one_or_none()
                if doc is not None and doc.status != "pending":
                    retry_failed = doc.status == "failed" and doc.fetched_at and \
                        utcnow() - ensure_utc(doc.fetched_at) > timedelta(hours=6)
                    if not retry_failed:
                        continue
                if budget <= 0:
                    break
                budget -= 1
                self.analyse_one(t, du["kind"], du["url"], doc)

    def analyse_one(self, t: Tender, kind: str, url: str, doc: Optional[TenderDocument]):
        if t.source_key in self.down:
            return
        a = self.adapter(t.source_key)
        if doc is None:
            doc = TenderDocument(tender_id=t.id, kind=kind, url=url, status="pending", extracted_fields={})
            self.db.add(doc)
        try:
            data = a.fetch_bytes(url) if a else b""
        except SourceUnavailable as e:
            self.down.add(t.source_key)              # leave the document pending for the next scan
            self.error("document", f"source unavailable, remaining documents deferred: {e}", t.source_key)
            return
        except Exception as e:
            doc.status, doc.error, doc.fetched_at = "failed", f"download failed: {e}"[:500], utcnow()
            self.error("document", str(e), t.source_key, t.bid_number)
            return
        res = extract_pdf(data)
        doc.status, doc.sha256, doc.pages, doc.error, doc.fetched_at = res.status, res.sha256, res.pages, res.error, utcnow()
        self.stats["documents_analysed"] += 1
        d = dict(t.details or {})
        d["doc_status"] = res.status
        if res.status != "extracted":
            t.details = d
            self.refresh_derived(t)
            return
        doc.text_excerpt = res.text[:4000]
        fields = parse_gem_bid_text(res.text) if t.source_key == "gem_bidplus" else {}
        doc.extracted_fields = fields
        old_fields = d.get("doc_fields") or {}
        if kind in ("bid_document",) or not old_fields:
            if old_fields:
                for c in diff_eligibility(old_fields, fields):
                    self.event(t, c)
            d["doc_fields"] = {k: v for k, v in fields.items() if v not in (None, "", [])}
            d["doc_text"] = res.text[:DOC_TEXT_KEEP]
            d["eligibility"] = eligibility_summary(fields)
            d["requirements"] = requirements_summary(fields)
            pins = fields.get("consignee_pins") or []
            d["pins"] = pins
            t.details = d
            self.apply_doc_fields(t, fields)
            self.score(t)
            self.touched[t.id] = t
        else:
            t.details = d

    def apply_doc_fields(self, t: Tender, f: Dict):
        """Only facts printed in the official document are copied; anything absent stays null."""
        if f.get("estimated_value"):
            t.estimated_value = f["estimated_value"]
        if f.get("emd_amount"):
            t.emd = f["emd_amount"]
        if f.get("contract_period"):
            t.contract_duration = f["contract_period"]
        if f.get("epbg_percent"):
            t.performance_security = f"{f['epbg_percent']:g}% ePBG" + (f" for {int(f['epbg_months'])} months" if f.get("epbg_months") else "")
        if f.get("organisation"):
            t.organization = f["organisation"]
        if f.get("department") and not t.department:
            t.department = f["department"]
        if f.get("ministry") and not t.ministry:
            t.ministry = f["ministry"]
        if f.get("total_quantity") and not t.quantity:
            t.quantity = f["total_quantity"]
        if f.get("bid_opening") and not t.opening_at:
            t.opening_at = parse_ist_string(f["bid_opening"])
        pins = f.get("consignee_pins") or []
        states = [s for s in (state_from_pin(p) for p in pins) if s]
        if states:
            t.state = states[0]
            t.location = ", ".join(sorted(set(states))) + f" (PIN {', '.join(pins[:3])}{'…' if len(pins) > 3 else ''})"
        elif f.get("office"):
            t.location = t.location or f["office"]
        if not t.state:
            t.state = state_from_text(t.ministry, t.department, t.organization)

    # ------------------------------------------------------------------ refresh tracked tenders
    def refresh_tracked(self):
        db = self.db
        now = utcnow()
        threshold = int(cfg.get(db, "matching").get("threshold", 75))
        rows = db.execute(select(Tender).where(
            or_(Tender.watched.is_(True), Tender.relevance_score >= min(threshold, RELEVANT_MIN_SCORE)),
            Tender.status.not_in(["cancelled"]),
        ).order_by(Tender.last_refreshed_at.is_(None).desc(), Tender.last_refreshed_at)).scalars().all()
        budget = self.s.max_refreshes_per_scan
        for t in rows:
            if budget <= 0 or not self._time_left():
                break
            if not self._refresh_due(t, now):
                continue
            if t.source_key in self.down:
                continue
            a = self.adapter(t.source_key)
            if a is None or not getattr(a, "supports_refresh", True):
                continue
            budget -= 1
            try:
                res = a.refresh(t)
            except SourceUnavailable as e:
                self.down.add(t.source_key)
                self.error("refresh", f"source unavailable, remaining refreshes deferred: {e}", t.source_key)
                continue
            except Exception as e:
                self.error("refresh", f"{type(e).__name__}: {e}", t.source_key, t.bid_number)
                continue
            t.last_refreshed_at = utcnow()
            self.stats["refreshed"] += 1
            if not res.found:
                continue
            old = self.tender_state(t)
            changes = []
            if res.raw is not None:
                if res.raw.ra_number and res.raw.ra_number != t.ra_number:
                    changes.append(Change("reverse_auction_started", "ra_number", t.ra_number, res.raw.ra_number, False))
                    changes += [c for c in diff_listing(old, self.raw_state(res.raw)) if c.event_type != "deadline_changed"]
                else:
                    changes += diff_listing(old, self.raw_state(res.raw))
                self.apply_raw(t, res.raw)
            d = dict(t.details or {})
            corr = diff_corrigenda(int(d.get("corrigenda_count") or 0), res.corrigenda or [])
            if res.corrigenda is not None and res.has_corrigendum:
                d["corrigenda_count"] = max(len(res.corrigenda), int(d.get("corrigenda_count") or 0))
                d["corrigenda"] = res.corrigenda[:20]
            if corr:
                changes += corr
                # amended terms: re-read the bid document on the next document pass
                for doc in t.documents:
                    if doc.kind == "bid_document":
                        doc.status = "pending"
            if res.has_representation is not None:
                d["has_representation"] = res.has_representation
            if res.outcome:
                changes += diff_outcome(d.get("outcome"), res.outcome)
                d["outcome"] = res.outcome
            t.details = d
            for c in changes:
                self.event(t, c)
            if changes:
                self.observe(t)
                self.touched[t.id] = t
            self.refresh_derived(t)
        db.flush()
        # re-analyse documents invalidated by corrigenda (within remaining budget)
        self.analyse_documents()

    @staticmethod
    def _refresh_due(t: Tender, now) -> bool:
        last = ensure_utc(t.last_refreshed_at)
        if last is None:
            return True
        dl = ensure_utc(t.deadline_at)
        if t.status == "awarded" and ((t.details or {}).get("outcome") or {}).get("ranks"):
            return now - last > timedelta(days=3)
        if dl and abs((dl - now).total_seconds()) < 48 * 3600:
            interval = timedelta(minutes=55)          # close to deadline / just closed: hourly
        elif t.status in ("closed", "in_evaluation"):
            interval = timedelta(hours=6)             # waiting for result publication
        else:
            interval = timedelta(hours=4)
        if dl and now - dl > timedelta(days=120):
            return False                              # stale; stop polling
        return now - last >= interval

    def close_expired(self):
        now = utcnow()
        rows = self.db.execute(select(Tender).where(Tender.status == "active", Tender.deadline_at.is_not(None),
                                                    Tender.deadline_at < now - timedelta(minutes=30))).scalars().all()
        for t in rows:
            if ensure_utc(t.deadline_at) < now - timedelta(minutes=30):
                self.event(t, Change("status_changed", "status", "active", "closed", False))
                t.status, t.stage = "closed", "Deadline passed (awaiting portal update)"
                self.refresh_derived(t)

    # ------------------------------------------------------------------ notifications
    def notify(self):
        db = self.db
        db.flush()
        now = utcnow()
        for t in self.touched.values():
            dl = ensure_utc(t.deadline_at)
            if t.status == "active" and (dl is None or dl > now):
                try:
                    if nq.notify_new_tender(db, t):
                        self.stats["notifications_queued"] += 1
                except Exception as e:
                    self.error("notify", f"render/queue failed: {e}", t.source_key, t.bid_number, traceback.format_exc())
        for t, c in self.pending_changes:
            if c.event_type == "deadline_changed" and t.status != "active":
                continue
            summary = {"deadline_changed": f"Deadline changed from {c.old} to {c.new}",
                       "cancelled": "The tender has been cancelled on the portal",
                       "outcome_available": f"Outcome published — {c.new}",
                       "corrigendum_published": f"New corrigendum: {c.new}",
                       "eligibility_changed": f"{c.field.replace('_', ' ')} changed from {c.old} to {c.new}",
                       }.get(c.event_type, f"{c.field} changed from {c.old} to {c.new}")
            try:
                if nq.notify_change(db, t, EVENT_TO_PREF[c.event_type], summary,
                                    f"{c.event_type}|{c.field}|{c.old}|{c.new}"):
                    self.stats["notifications_queued"] += 1
            except Exception as e:
                self.error("notify", f"render/queue failed: {e}", t.source_key, t.bid_number, traceback.format_exc())
        db.flush()

    def summaries(self):
        try:
            send_due_summaries(self.db)
        except Exception as e:
            self.error("notify", f"summary failed: {e}", detail=traceback.format_exc())


def send_due_summaries(db: Session, now=None) -> List[str]:
    """Daily/weekly digests, created at most once per period (dedupe key per date/week)."""
    now = now or utcnow()
    ist = to_ist(now)
    prefs = cfg.get(db, "notifications")
    made = []
    for kind, days in (("daily_summary", 1), ("weekly_summary", 7)):
        p = prefs.get(kind) or {}
        if not p.get("enabled"):
            continue
        if ist.hour != int(p.get("hour_ist", 8)):
            continue
        if kind == "weekly_summary" and ist.weekday() != int(p.get("weekday", 0)):
            continue
        period_key = ist.strftime("%Y-%m-%d") if days == 1 else f"{ist.isocalendar()[0]}-W{ist.isocalendar()[1]:02d}"
        start = now - timedelta(days=days)
        ctx = summary_context(db, start, now, "Daily" if days == 1 else "Weekly")
        body = nq.render_summary(ctx)
        subject = f"{nq.SUBJECT_PREFIX} {ctx['period']} summary — {len(ctx['new'])} new relevant tender(s)"
        if nq.enqueue(db, f"{kind}:{period_key}", kind, subject, body, None):
            made.append(kind)
    return made


def summary_context(db: Session, start, end, period: str) -> Dict:
    threshold = int(cfg.get(db, "matching").get("threshold", 75))
    new = db.execute(select(Tender).where(Tender.first_seen_at >= start, Tender.relevance_score >= threshold)
                     .order_by(Tender.relevance_score.desc()).limit(30)).scalars().all()
    upd = db.execute(select(TenderEvent, Tender).join(Tender, Tender.id == TenderEvent.tender_id)
                     .where(TenderEvent.observed_at >= start, TenderEvent.significant.is_(True),
                            Tender.relevance_score >= threshold).order_by(TenderEvent.observed_at.desc()).limit(30)).all()
    now = utcnow()
    closing = db.execute(select(Tender).where(Tender.status == "active", Tender.relevance_score >= threshold,
                                              Tender.deadline_at.between(now, now + timedelta(days=3)))
                         .order_by(Tender.deadline_at)).scalars().all()
    scans = db.execute(select(ScanExecution).where(ScanExecution.slot_at >= start)).scalars().all()
    base = get_settings().public_base_url.rstrip("/")

    def line(t):
        return {"score": t.relevance_score, "title": t.title[:110], "number": t.bid_number or t.external_id,
                "deadline": fmt_ist(t.deadline_at), "url": f"{base}/tenders/{t.id}"}
    return {"period": period, "start": fmt_ist(start), "end": fmt_ist(end), "new": [line(t) for t in new],
            "updates": [{"event": e.event_type.replace("_", " "), "title": t.title[:90],
                         "number": t.bid_number or t.external_id, "detail": (e.new_value or "")[:160]} for e, t in upd],
            "closing": [line(t) for t in closing],
            "scans_ok": sum(1 for s in scans if s.status in ("succeeded", "partial")),
            "scans_failed": sum(1 for s in scans if s.status == "failed"),
            "scans_missed": sum(1 for s in scans if s.status == "missed"), "dashboard": base}


def rescore_all(db: Session, only_active: bool = False) -> int:
    """Re-apply the current keyword/category settings to stored tenders (after dashboard edits)."""
    models = load_models(db)
    enabled = set(cfg.get(db, "matching").get("enabled_categories") or [])
    for m in models:
        m.enabled = m.enabled and m.code in enabled
    dummy = Scan.__new__(Scan)
    dummy.db, dummy.models, dummy.embedder = db, models, None
    q = select(Tender)
    if only_active:
        q = q.where(Tender.status == "active")
    n = 0
    for t in db.execute(q).scalars():
        Scan.score(dummy, t)
        n += 1
    return n
