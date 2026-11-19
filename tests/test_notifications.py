"""Requirements 10–11: email formatting and retry handling (plus dedupe, holds and delivery tracking)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import timedelta

from sqlalchemy import func, select

from backend import settings_service as cfg
from core.timeutil import utcnow
from database.schema.models import EmailDeliveryAttempt, EmailNotification, SuppressedNotification, Tender
from notifications.email_sender.senders import ConsoleSender, SendResult, verify_resend_webhook
from notifications.notification_queue import queue as nq


def _tender(db, **kw):
    details = kw.pop("details", {})
    t = Tender(source_key="gem_bidplus", external_id=kw.pop("external_id", "9000001"),
               bid_number="GEM/2026/B/9000001", title=kw.pop("title", "Repair and overhauling of DG set <b>125 KVA</b>"),
               organization="Indian Army", department="Department of Military Affairs", ministry="Ministry of Defence",
               status="active", stage="Open for bidding", relevance_score=92, best_category="F",
               match_explanation="Very high match for Car & Generator Repair: phrase 'repair and overhauling of dg set'.",
               deadline_at=utcnow() + timedelta(days=4), published_at=utcnow() - timedelta(days=1),
               official_url="https://bidplus.gem.gov.in/showbidDocument/9000001",
               document_url="https://bidplus.gem.gov.in/showbidDocument/9000001", details=details, **kw)
    db.add(t)
    db.flush()
    return t


def _verify_recipient(db, email="alerts@example.com"):
    cfg.put(db, "recipient", {"email": email, "verified": True})


# --- 10. email formatting -------------------------------------------------------------------------------
def test_alert_email_contains_required_sections(db):
    t = _tender(db, estimated_value=850000, contract_duration="1 Year(s)",
                details={"requirements": ["EMD: ₹17,000"], "eligibility": ["Turnover 10 Lakh"], "issues": ["Short window"]})
    subject, text, html = nq.render_tender_email(db, t, "new_tender")
    assert subject.startswith("[OPvickerG BidRadar] New Relevant Government Tender — Car & Generator Repair —")
    for part in ("NEW GOVERNMENT PROCUREMENT OPPORTUNITY DETECTED", "Tender ID: GEM/2026/B/9000001",
                 "Organization: Indian Army / Department of Military Affairs / Ministry of Defence",
                 "Relevance Score: 92/100 (very strong)", "Estimated Value: ₹850,000", "Contract Duration: 1 Year(s)",
                 "WHY THIS TENDER MATCHES:", "IMPORTANT REQUIREMENTS:\n- EMD: ₹17,000", "ELIGIBILITY:\n- Turnover 10 Lakh",
                 "POTENTIAL ISSUES:\n- Short window", "View Tender: https://bidplus.gem.gov.in/",
                 "Open in BidRadar: http://testserver/tenders/", "IST", "Automated Government Tender Intelligence"):
        assert part in text, part
    assert "<b>125 KVA</b>" not in html and "&lt;b&gt;125 KVA&lt;/b&gt;" in html     # HTML-escaped
    assert "Repair and overhauling of DG set" in html


def test_missing_values_are_stated_not_invented(db):
    t = _tender(db)
    _, text, _ = nq.render_tender_email(db, t, "new_tender")
    assert "Estimated Value: Not Disclosed" in text and "EMD: Unknown" in text
    assert "Contract Duration: Not stated" in text and "- Not extracted (see official document)" in text


def test_change_email_states_what_changed(db):
    t = _tender(db)
    _, text, _ = nq.render_tender_email(db, t, "deadline_changed", "Deadline changed from 20 Nov to 27 Nov")
    assert text.startswith("DEADLINE CHANGED ON A RELEVANT TENDER") and "WHAT CHANGED:\nDeadline changed from" in text


# --- holds & dedupe -------------------------------------------------------------------------------------
def test_alerts_held_without_verified_recipient_and_released_after(db):
    t = _tender(db)
    n = nq.notify_new_tender(db, t)
    assert n.status == "held_no_recipient" and n.recipient is None
    cfg.put(db, "recipient", {"email": "alerts@example.com", "verified": False})
    t2 = _tender(db, external_id="9000002")
    assert nq.notify_new_tender(db, t2).status == "held_unverified"
    assert nq.process_pending(db, sender=ConsoleSender())["sent"] == 0        # nothing is sent while held
    _verify_recipient(db)
    assert nq.release_held(db) == 2
    assert nq.process_pending(db, sender=ConsoleSender())["sent"] == 2


def test_duplicate_notifications_suppressed(db):
    t = _tender(db)
    assert nq.notify_new_tender(db, t) is not None
    assert nq.notify_new_tender(db, t) is None
    assert db.scalar(select(func.count()).select_from(EmailNotification)) == 1
    assert db.scalar(select(func.count()).select_from(SuppressedNotification)) == 1
    assert nq.notify_change(db, t, "deadline_changed", "x", "fp-1") is not None
    assert nq.notify_change(db, t, "deadline_changed", "x", "fp-1") is None
    assert nq.notify_change(db, t, "deadline_changed", "y", "fp-2") is not None   # a later, different change


def test_below_threshold_and_geography_filtered(db):
    t = _tender(db)
    t.relevance_score = 60
    assert nq.notify_new_tender(db, t) is None
    t.relevance_score = 92
    t.state = "Kerala"
    cfg.put(db, "geography", {"states": ["Delhi"], "pin_prefixes": []})
    assert nq.notify_new_tender(db, t) is None
    t.details = {"pins": ["110010"]}
    assert nq.notify_new_tender(db, t) is not None


# --- 11. retry handling ---------------------------------------------------------------------------------
class Flaky:
    name = "flaky"

    def __init__(self, failures, retryable=True):
        self.failures, self.retryable, self.calls = failures, retryable, 0

    def send(self, to, subject, text, html, idempotency_key):
        self.calls += 1
        if self.calls <= self.failures:
            return SendResult(ok=False, provider=self.name, http_status=503, detail="unavailable", retryable=self.retryable)
        return SendResult(ok=True, provider=self.name, message_id=f"m-{self.calls}")


def _due_all(db):
    for n in db.execute(select(EmailNotification)).scalars():
        if n.next_attempt_at:
            n.next_attempt_at = utcnow() - timedelta(seconds=1)


def test_transient_failure_is_retried_with_backoff_then_accepted(db):
    _verify_recipient(db)
    n = nq.notify_new_tender(db, _tender(db))
    s = Flaky(failures=2)
    assert nq.process_pending(db, sender=s) == {"sent": 0, "failed": 0, "retry": 1}
    assert n.status == "pending" and n.attempts == 1 and n.next_attempt_at > utcnow()
    assert nq.process_pending(db, sender=s)["retry"] == 0 and s.calls == 1     # not due yet: no attempt
    _due_all(db)
    nq.process_pending(db, sender=s)
    _due_all(db)
    nq.process_pending(db, sender=s)
    assert n.status == "accepted" and n.attempts == 3 and n.provider_message_id == "m-3"
    assert n.delivered_at is None                                         # accepted != delivered
    outcomes = [a.outcome for a in db.execute(select(EmailDeliveryAttempt).order_by(EmailDeliveryAttempt.id)).scalars()]
    assert outcomes == ["rejected", "rejected", "accepted"]


def test_permanent_failure_and_attempt_limit(db):
    _verify_recipient(db)
    n1 = nq.notify_new_tender(db, _tender(db))
    nq.process_pending(db, sender=Flaky(failures=99, retryable=False))
    assert n1.status == "failed"
    n2 = nq.notify_new_tender(db, _tender(db, external_id="9000003"))
    s = Flaky(failures=99)
    for _ in range(6):
        _due_all(db)
        nq.process_pending(db, sender=s)
    assert n2.status == "failed" and n2.attempts == 5


def test_delivery_status_only_from_signed_provider_event(db):
    _verify_recipient(db)
    n = nq.notify_new_tender(db, _tender(db))
    nq.process_pending(db, sender=Flaky(failures=0))
    secret_bytes = b"0123456789abcdef0123456789abcdef"
    secret = "whsec_" + base64.b64encode(secret_bytes).decode()
    body = ('{"type":"email.delivered","data":{"email_id":"%s"}}' % n.provider_message_id).encode()
    ts = str(int(time.time()))
    sig = base64.b64encode(hmac.new(secret_bytes, f"msg_1.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    good = {"svix-id": "msg_1", "svix-timestamp": ts, "svix-signature": f"v1,{sig}"}
    assert verify_resend_webhook(body, good, secret)
    assert not verify_resend_webhook(body + b" ", good, secret)
    assert not verify_resend_webhook(body, dict(good, **{"svix-timestamp": str(int(time.time()) - 3600)}), secret)
    assert not verify_resend_webhook(body, good, None)
    assert nq.apply_provider_event(db, {"type": "email.delivered", "data": {"email_id": n.provider_message_id}})
    assert n.status == "delivered" and n.delivered_at is not None


def test_rule_change_rescores_and_cancels_alerts_that_no_longer_qualify(db, monkeypatch):
    from scanner.discovery import pipeline
    good = _tender(db, external_id="R1", title="Repair and overhauling of DG set")
    bad = _tender(db, external_id="R2", title="Electric Lift")      # stored with a stale high score
    bad.description = "stainless steel face plate 1.5mm"
    n_good, n_bad = nq.notify_new_tender(db, good), nq.notify_new_tender(db, bad)
    assert n_good.status == n_bad.status == "held_no_recipient"
    monkeypatch.setattr(pipeline, "scoring_fingerprint", lambda: "rules-v2")
    assert pipeline.rescore_if_rules_changed(db) >= 2
    assert bad.relevance_score < 50 and good.relevance_score >= 75
    assert n_bad.status == "cancelled" and n_good.status == "held_no_recipient"
    assert pipeline.rescore_if_rules_changed(db) == 0                # same rules: no second pass


def test_release_skips_closed_tenders(db):
    t = _tender(db)
    n = nq.notify_new_tender(db, t)
    t.deadline_at = utcnow() - timedelta(hours=1)
    _verify_recipient(db)
    nq.release_held(db)
    assert n.status == "cancelled" and "closed" in n.last_error
