"""Notification queue: render -> dedupe -> hold/queue -> send with retries -> track provider events."""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import timedelta
from typing import Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import settings_service as cfg
from core.config import get_settings
from core.geo import matches_geography
from core.logs import log
from core.timeutil import ensure_utc, fmt_ist, utcnow
from database.schema.models import (EmailDeliveryAttempt, EmailNotification, Source, SuppressedNotification,
                                    Tender)
from notifications.email_sender.senders import EmailSender, get_sender
from scanner.bid_analysis.relevance import band

logger = logging.getLogger("bidradar.notify")
_TPL = os.path.join(os.path.dirname(__file__), "..", "email_templates")
_env = Environment(loader=FileSystemLoader(_TPL), autoescape=select_autoescape(["html", "html.j2"]))
CATEGORY_NAMES = {"A": "CRDI & Diesel Injection", "B": "Cylinder Head Work", "C": "Engine Machining",
                  "D": "Automotive Scanning & Diagnostics", "E": "Plates Facing & Grinding",
                  "F": "Car & Generator Repair"}
HEADINGS = {"new_tender": "NEW GOVERNMENT PROCUREMENT OPPORTUNITY DETECTED",
            "deadline_changed": "DEADLINE CHANGED ON A RELEVANT TENDER",
            "corrigendum": "CORRIGENDUM / AMENDMENT PUBLISHED",
            "eligibility_changed": "ELIGIBILITY REQUIREMENTS CHANGED",
            "cancelled": "RELEVANT TENDER CANCELLED",
            "outcome": "TENDER OUTCOME PUBLISHED"}
SUBJECT_PREFIX = "[OPvickerG BidRadar]"
FINAL_SENT = {"accepted", "delivered"}


def _money(v) -> str:
    return f"₹{float(v):,.0f}" if v not in (None, "") else "Not Disclosed"


def tender_links(t: Tender, base_url: str) -> Dict[str, Optional[str]]:
    d = t.details or {}
    corr = None
    if t.source_key == "gem_bidplus" and (d.get("corrigenda_count") or 0) > 0:
        corr = "https://bidplus.gem.gov.in/all-bids (search the bid number → View Corrigendum)"
    return {"view": t.official_url, "document": t.document_url, "corrigenda": corr,
            "dashboard": f"{base_url.rstrip('/')}/tenders/{t.id}"}


def render_tender_email(db: Session, t: Tender, event_type: str, change_summary: Optional[str] = None):
    s = get_settings()
    d = t.details or {}
    src = db.execute(select(Source).where(Source.key == t.source_key)).scalar_one_or_none()
    category = CATEGORY_NAMES.get(t.best_category or "", "—")
    ctx = dict(
        heading=HEADINGS.get(event_type, "TENDER UPDATE"), change_summary=change_summary, t=t,
        source_name=src.name if src else t.source_key,
        org=" / ".join(x for x in [t.organization, t.department, t.ministry] if x) or "—",
        location=t.location or t.state or "Not extracted", category=category, band=band(t.relevance_score),
        published=fmt_ist(t.published_at), deadline=fmt_ist(t.deadline_at), est_value=_money(t.estimated_value),
        emd=_money(t.emd) if t.emd else ("Not applicable" if (d.get("doc_fields") or {}).get("emd_required") == "No" else "Unknown"),
        duration=t.contract_duration or "Not stated", status=t.stage or t.status,
        requirements=d.get("requirements") or [], eligibility=d.get("eligibility") or [], issues=d.get("issues") or [],
        links=tender_links(t, s.public_base_url), verified=fmt_ist(t.last_verified_at or t.last_seen_at, True),
        badge_bg="#15803d" if t.relevance_score >= 90 else "#1d4ed8" if t.relevance_score >= 75 else "#b45309")
    ctx["facts"] = [("Tender ID", (t.bid_number or t.external_id) + (f" · RA {t.ra_number}" if t.ra_number else "")),
                    ("Organization", ctx["org"]), ("Location", ctx["location"]), ("Published", ctx["published"]),
                    ("Submission deadline", ctx["deadline"]), ("Estimated value", ctx["est_value"]),
                    ("EMD", ctx["emd"]), ("Contract duration", ctx["duration"]), ("Status", ctx["status"]),
                    ("Source", ctx["source_name"])]
    ctx["sections"] = [("WHY THIS TENDER MATCHES", t.match_explanation or "—"),
                       ("IMPORTANT REQUIREMENTS", ctx["requirements"]), ("ELIGIBILITY", ctx["eligibility"]),
                       ("POTENTIAL ISSUES", ctx["issues"])]
    text = _env.get_template("tender_alert.txt.j2").render(**ctx)
    html = _env.get_template("tender_alert.html.j2").render(**ctx)
    label = {"new_tender": "New Relevant Government Tender", "deadline_changed": "Deadline Changed",
             "corrigendum": "Corrigendum Published", "eligibility_changed": "Eligibility Changed",
             "cancelled": "Tender Cancelled", "outcome": "Outcome Published"}.get(event_type, "Tender Update")
    subject = f"{SUBJECT_PREFIX} {label} — {category} — {t.title[:70]}"
    return subject, text, html


def _initial_status(db: Session) -> (str, Optional[str]):
    rec = cfg.get(db, "recipient")
    prefs = cfg.get(db, "notifications")
    if not prefs.get("enabled", True):
        return "held_disabled", rec.get("email")
    if not rec.get("email"):
        return "held_no_recipient", None
    if not rec.get("verified"):
        return "held_unverified", rec.get("email")
    return "pending", rec["email"]


def enqueue(db: Session, dedupe_key: str, event_type: str, subject: str, text: str, html: Optional[str],
            tender_id: Optional[int] = None, force_recipient: Optional[str] = None) -> Optional[EmailNotification]:
    """Create a notification unless an identical one exists. Returns None when suppressed as duplicate."""
    existing = db.execute(select(EmailNotification).where(EmailNotification.dedupe_key == dedupe_key)).scalar_one_or_none()
    if existing is not None:
        db.add(SuppressedNotification(dedupe_key=dedupe_key, reason="duplicate"))
        return None
    if force_recipient:
        status, recipient = "pending", force_recipient
    else:
        status, recipient = _initial_status(db)
    n = EmailNotification(dedupe_key=dedupe_key, tender_id=tender_id, event_type=event_type, subject=subject,
                          body_text=text, body_html=html, recipient=recipient, status=status,
                          next_attempt_at=utcnow() if status == "pending" else None)
    try:
        with db.begin_nested():
            db.add(n)
    except IntegrityError:
        db.add(SuppressedNotification(dedupe_key=dedupe_key, reason="duplicate_race"))
        return None
    return n


def tender_qualifies(db: Session, t: Tender) -> bool:
    m = cfg.get(db, "matching")
    geo = cfg.get(db, "geography")
    if t.relevance_score < int(m.get("threshold", 75)):
        return False
    if t.best_category and t.best_category not in (m.get("enabled_categories") or []):
        return False
    pins = (t.details or {}).get("pins") or []
    return matches_geography(t.state, pins, geo.get("states"), geo.get("pin_prefixes"))


def notify_new_tender(db: Session, t: Tender) -> Optional[EmailNotification]:
    prefs = cfg.get(db, "notifications")
    if not prefs.get("new_tender", True) or not tender_qualifies(db, t):
        return None
    subject, text, html = render_tender_email(db, t, "new_tender")
    return enqueue(db, f"new:{t.source_key}:{t.external_id}", "new_tender", subject, text, html, t.id)


def notify_change(db: Session, t: Tender, pref_key: str, change_summary: str, fingerprint: str) -> Optional[EmailNotification]:
    prefs = cfg.get(db, "notifications")
    if not prefs.get(pref_key, True):
        return None
    if not (t.watched or tender_qualifies(db, t)):
        return None
    subject, text, html = render_tender_email(db, t, pref_key, change_summary)
    key = f"{pref_key}:{t.source_key}:{t.external_id}:{hashlib.sha1(fingerprint.encode()).hexdigest()[:16]}"
    return enqueue(db, key, pref_key, subject, text, html, t.id)


def release_held(db: Session) -> int:
    """After a recipient is configured and verified, held alerts become pending (newest 20 only, to avoid
    flooding the inbox with stale items; older ones are cancelled and visible in the dashboard)."""
    status, recipient = _initial_status(db)
    if status != "pending":
        return 0
    held = db.execute(select(EmailNotification).where(EmailNotification.status.in_(
        ["held_no_recipient", "held_unverified", "held_disabled"])).order_by(EmailNotification.created_at.desc())).scalars().all()
    released = 0
    for n in held:
        t = db.get(Tender, n.tender_id) if (n.tender_id and n.event_type == "new_tender") else None
        if t is not None and not tender_qualifies(db, t):
            n.status, n.last_error = "cancelled", "tender no longer matches the current settings"
            continue
        if t is not None and (t.status != "active" or (t.deadline_at and ensure_utc(t.deadline_at) <= utcnow())):
            n.status, n.last_error = "cancelled", "tender closed before the alert could be sent"
            continue
        if released < 20 and n.event_type not in ("daily_summary", "weekly_summary"):
            released += 1
            n.status, n.recipient, n.next_attempt_at = "pending", recipient, utcnow()
        else:
            n.status, n.last_error = "cancelled", "superseded: released only the newest 20 held alerts"
    return len(held)


def process_pending(db: Session, sender: Optional[EmailSender] = None, limit: int = 50) -> Dict[str, int]:
    s = get_settings()
    sender = sender or get_sender()
    now = utcnow()
    stats = {"sent": 0, "failed": 0, "retry": 0}
    rows = db.execute(select(EmailNotification).where(EmailNotification.status == "pending")
                      .order_by(EmailNotification.created_at).limit(limit)).scalars().all()
    for n in rows:
        if n.next_attempt_at and n.next_attempt_at.replace(tzinfo=n.next_attempt_at.tzinfo or now.tzinfo) > now:
            continue
        n.status, n.attempts = "sending", n.attempts + 1
        db.flush()
        res = sender.send(n.recipient, n.subject, n.body_text, n.body_html, idempotency_key=n.dedupe_key)
        db.add(EmailDeliveryAttempt(notification_id=n.id, provider=res.provider,
                                    outcome="accepted" if res.ok else ("error" if res.http_status is None else "rejected"),
                                    http_status=res.http_status, detail=res.detail))
        n.provider, n.updated_at = res.provider, utcnow()
        if res.ok:
            n.status, n.provider_message_id, n.last_error = "accepted", res.message_id, None
            stats["sent"] += 1
        elif res.retryable and n.attempts < s.email_max_attempts:
            n.status, n.last_error = "pending", res.detail
            n.next_attempt_at = utcnow() + timedelta(minutes=min(240, 5 * (3 ** (n.attempts - 1))))
            stats["retry"] += 1
        else:
            n.status, n.last_error = "failed", res.detail
            stats["failed"] += 1
        log(logger, logging.INFO, "notification processed", id=n.id, status=n.status, provider=res.provider)
    return stats


def apply_provider_event(db: Session, event: Dict) -> bool:
    """Resend webhook events: email.delivered / email.bounced / email.complained / email.delivery_delayed."""
    etype = event.get("type", "")
    data = event.get("data") or {}
    mid = data.get("email_id") or data.get("id")
    if not mid:
        return False
    n = db.execute(select(EmailNotification).where(EmailNotification.provider_message_id == mid)).scalar_one_or_none()
    if n is None:
        return False
    db.add(EmailDeliveryAttempt(notification_id=n.id, provider="resend", outcome="provider_event", detail=etype))
    if etype == "email.delivered":
        n.status, n.delivered_at = "delivered", utcnow()
    elif etype == "email.bounced":
        n.status, n.last_error = "bounced", str(data.get("bounce") or "")[:300]
    elif etype == "email.complained":
        n.status = "complained"
    n.updated_at = utcnow()
    return True


def build_simple(subject: str, body: str) -> (str, str):
    return f"{SUBJECT_PREFIX} {subject}", _env.get_template("simple.txt.j2").render(body=body)


def render_summary(ctx: Dict) -> str:
    return _env.get_template("summary.txt.j2").render(**ctx)
