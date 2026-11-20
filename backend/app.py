"""OPvickerG BidRadar — dashboard + JSON API (FastAPI, server-rendered Jinja templates, no client JS required)."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from backend import auth
from backend import settings_service as cfg
from core.config import get_settings
from core.logs import log, setup_logging
from core.timeutil import IST, ensure_utc, fmt_ist, next_slot, to_ist, utcnow
from database.schema.models import (BidObservation, Category, EmailDeliveryAttempt, EmailNotification, Keyword,
                                    ScanError, ScanExecution, SchedulerLock, Source, SuppressedNotification,
                                    SystemEvent, Tender, TenderDocument, TenderEvent, TenderMatch)
from database.session import new_session
from notifications.email_sender.senders import get_sender, verify_resend_webhook
from notifications.notification_queue import queue as nq
from scanner.bid_analysis.relevance import band

setup_logging()
logger = logging.getLogger("bidradar.web")
S = get_settings()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATEGORY_NAMES = nq.CATEGORY_NAMES
KIND_LABELS = {"phrase": "Exact phrases", "synonym": "Synonyms / related terms", "discovery": "Portal search terms",
               "object": "Concept objects", "action": "Concept actions", "negative": "Exclusions (negative context)"}
VERIFY_TTL = timedelta(hours=48)

_secret = S.session_secret
if not _secret and not S.database_url.startswith("sqlite"):
    # Derived from the (secret) database URL: stable across restarts, never stored or shown anywhere.
    _secret = hashlib.sha256(("bidradar-session-v1:" + S.database_url).encode()).hexdigest()
if not _secret:
    if S.environment == "production":
        raise RuntimeError("SESSION_SECRET (or a PostgreSQL DATABASE_URL) must be set in production")
    _secret = secrets.token_urlsafe(32)          # dev only: sessions reset on restart

app = FastAPI(title="OPvickerG BidRadar", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=_secret, session_cookie="bidradar_session", max_age=12 * 3600,
                   same_site="lax", https_only=S.secure_cookies)
app.mount("/static", StaticFiles(directory=os.path.join(ROOT, "frontend", "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(ROOT, "frontend", "templates"))


@app.on_event("startup")
def _start_background_scanner() -> None:
    """Single-service mode (free hosting): the 30-minute scanner runs in a thread of the web app."""
    if S.run_scheduler:
        from scanner.scheduler import worker
        worker.start_in_background(keepalive_url=f"{S.public_base_url.rstrip('/')}/healthz" if S.keepalive else None)


# --------------------------------------------------------------------------- template helpers
def _money(v) -> str:
    if v in (None, ""):
        return "—"
    v = float(v)
    if v >= 1e7:
        return f"₹{v / 1e7:,.2f} Cr"
    if v >= 1e5:
        return f"₹{v / 1e5:,.2f} L"
    return f"₹{v:,.0f}"


def _until(dt) -> str:
    d = ensure_utc(dt)
    if not d:
        return "—"
    delta = d - utcnow()
    secs = int(delta.total_seconds())
    past = secs < 0
    secs = abs(secs)
    if secs < 3600:
        s = f"{secs // 60} min"
    elif secs < 86400 * 2:
        s = f"{secs // 3600} h"
    else:
        s = f"{secs // 86400} d"
    return f"{s} ago" if past else f"in {s}"


def _score_cls(score: int) -> str:
    return {"very strong": "s-vh", "strong": "s-h", "potentially relevant": "s-m", "weak": "s-l"}.get(band(score or 0), "s-x")


templates.env.filters.update(ist=fmt_ist, money=_money, until=_until, score_cls=_score_cls, band=band)
templates.env.globals.update(CATEGORY_NAMES=CATEGORY_NAMES, app_name=S.app_name)


def get_db():
    db = new_session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class NeedsLogin(Exception):
    pass


@app.exception_handler(NeedsLogin)
async def _needs_login(request: Request, exc: NeedsLogin):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "authentication required"}, status_code=401)
    return RedirectResponse("/login?" + urlencode({"next": request.url.path}), status_code=303)


def require_user(request: Request) -> str:
    if not auth.is_logged_in(request):
        raise NeedsLogin()
    return request.session["user"]


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("Content-Security-Policy",
                            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if S.secure_cookies:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp


def flash(request: Request, msg: str, kind: str = "ok"):
    request.session.setdefault("flash", [])
    request.session["flash"] = request.session["flash"] + [{"m": msg, "k": kind}]


def render(request: Request, name: str, db: Session, **ctx):
    mon = cfg.get(db, "monitoring")
    rec = cfg.get(db, "recipient")
    base = dict(request=request, csrf=auth.csrf_token(request), user=request.session.get("user"),
                flashes=request.session.pop("flash", []), paused=mon.get("paused"), recipient=rec,
                nav=name.split(".")[0], now_ist=fmt_ist(utcnow()), email_provider=S.email_provider)
    status_code = ctx.pop("status_code", 200)
    base.update(ctx)
    return templates.TemplateResponse(request, name, base, status_code=status_code)


def back(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


# --------------------------------------------------------------------------- health & auth
@app.get("/healthz")
def healthz(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    last = db.execute(select(ScanExecution).where(ScanExecution.status.in_(["succeeded", "partial"]))
                      .order_by(ScanExecution.finished_at.desc()).limit(1)).scalar_one_or_none()
    return {"status": "ok", "time_ist": fmt_ist(utcnow(), True),
            "last_successful_scan_ist": fmt_ist(last.finished_at) if last else None}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", db: Session = Depends(get_db)):
    return render(request, "login.html", db, next=next if next.startswith("/") else "/",
                  configured=auth.auth_configured(), email_login=bool(S.admin_email),
                  password_login=bool(S.admin_password_hash),
                  dev_login_link=request.session.pop("dev_login_link", None))


@app.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/"),
                 csrf: str = Form(""), db: Session = Depends(get_db)):
    auth.check_csrf(request, csrf)
    ip = auth.client_ip(request)
    if auth.limiter.blocked(ip):
        flash(request, "Too many failed attempts. Try again in 15 minutes.", "err")
        return back("/login")
    if not auth.login(request, username.strip(), password):
        auth.limiter.fail(ip)
        log(logger, logging.WARNING, "login failed", ip=ip)
        flash(request, "Incorrect username or password.", "err")
        return back("/login")
    auth.limiter.reset(ip)
    return back(next if next.startswith("/") and not next.startswith("//") else "/")


LOGIN_LINK_TTL = timedelta(minutes=20)


@app.post("/login/email")
def login_email(request: Request, email: str = Form(""), csrf: str = Form(""), db: Session = Depends(get_db)):
    """Passwordless sign-in: email a one-time link to ADMIN_EMAIL. Same reply whatever address is typed."""
    auth.check_csrf(request, csrf)
    ip = auth.client_ip(request)
    if auth.limiter.blocked(ip):
        flash(request, "Too many attempts. Try again in 15 minutes.", "err")
        return back("/login")
    email = email.strip().lower()
    if S.admin_email and email == S.admin_email:
        token = secrets.token_urlsafe(32)
        cfg.put(db, "login_link", {"hash": hashlib.sha256(token.encode()).hexdigest(),
                                   "expires": (utcnow() + LOGIN_LINK_TTL).isoformat()})
        link = f"{S.public_base_url.rstrip('/')}/login/verify?token={token}"
        subject, body = nq.build_simple("Your sign-in link",
                                        f"Click to sign in to OPvickerG BidRadar (valid for 20 minutes, one use):\n\n{link}\n\n"
                                        "If you did not ask for this, ignore this email.")
        nq.enqueue(db, f"login:{hashlib.sha256(token.encode()).hexdigest()[:24]}", "login_link", subject, body, None,
                   force_recipient=S.admin_email)
        db.flush()
        nq.process_pending(db, sender=get_sender(), limit=5)
        if S.email_provider == "console" and S.environment != "production":
            request.session["dev_login_link"] = link      # local development only
    else:
        auth.limiter.fail(ip)
    flash(request, "If that address is allowed, a sign-in link is on its way. Check your inbox (and spam).")
    return back("/login")


@app.get("/login/verify")
def login_verify(request: Request, token: str = "", db: Session = Depends(get_db)):
    rec = cfg.get(db, "login_link") or {}
    ok = bool(token and rec.get("hash") and hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), rec["hash"]))
    if ok and rec.get("expires"):
        from datetime import datetime
        ok = utcnow() <= ensure_utc(datetime.fromisoformat(rec["expires"]))
    if not ok:
        flash(request, "That sign-in link is invalid or has expired. Request a new one.", "err")
        return back("/login")
    cfg.put(db, "login_link", {})                         # single use
    auth.login_as(request, S.admin_email)
    recipient = cfg.get(db, "recipient")
    if recipient.get("email") and recipient["email"].lower() == S.admin_email and not recipient.get("verified"):
        # the click proves control of this inbox, which is exactly what recipient verification checks
        recipient.update(verified=True, verified_at=utcnow().isoformat(), verification_token_hash=None)
        cfg.put(db, "recipient", recipient)
        released = nq.release_held(db)
        db.add(SystemEvent(level="info", event="recipient_verified", message="Alert recipient verified via sign-in link",
                           data={"released": released}))
        flash(request, "Signed in. Your email is confirmed, so tender alerts are now switched on.")
    return back("/")


@app.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    auth.check_csrf(request, csrf)
    request.session.clear()
    return back("/login")


# --------------------------------------------------------------------------- status helpers
def status_snapshot(db: Session) -> Dict:
    now = utcnow()
    last = db.execute(select(ScanExecution).where(ScanExecution.trigger != "missed")
                      .order_by(ScanExecution.id.desc()).limit(1)).scalar_one_or_none()
    last_ok = db.execute(select(ScanExecution).where(ScanExecution.status.in_(["succeeded", "partial"]))
                         .order_by(ScanExecution.finished_at.desc()).limit(1)).scalar_one_or_none()
    lock = db.get(SchedulerLock, "scan")
    running = bool(lock and lock.holder and ensure_utc(lock.expires_at) and ensure_utc(lock.expires_at) > now)
    day = now - timedelta(hours=24)
    counts = dict(db.execute(select(ScanExecution.status, func.count()).where(ScanExecution.slot_at >= day)
                             .group_by(ScanExecution.status)).all())
    stale = last_ok is None or (now - ensure_utc(last_ok.finished_at) > timedelta(minutes=75))
    return {"last": last, "last_ok": last_ok, "running": running, "lock": lock, "next_slot": next_slot(now),
            "counts24": counts, "stale": stale}


def threshold(db: Session) -> int:
    return int(cfg.get(db, "matching").get("threshold", 75))


# --------------------------------------------------------------------------- overview
@app.get("/", response_class=HTMLResponse)
def overview(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    now = utcnow()
    th = threshold(db)
    active_rel = select(Tender).where(Tender.status == "active", Tender.relevance_score >= th)
    kpi = {
        "active_relevant": db.scalar(select(func.count()).select_from(active_rel.subquery())),
        "new_24h": db.scalar(select(func.count()).select_from(Tender).where(Tender.first_seen_at >= now - timedelta(hours=24),
                                                                            Tender.relevance_score >= th)),
        "closing_3d": db.scalar(select(func.count()).select_from(Tender).where(
            Tender.status == "active", Tender.relevance_score >= th, Tender.deadline_at.between(now, now + timedelta(days=3)))),
        "tracked_total": db.scalar(select(func.count()).select_from(Tender)),
        "pending_mail": db.scalar(select(func.count()).select_from(EmailNotification).where(
            EmailNotification.status.in_(["pending", "held_no_recipient", "held_unverified", "held_disabled"]))),
    }
    latest = db.execute(active_rel.order_by(Tender.first_seen_at.desc()).limit(8)).scalars().all()
    closing = db.execute(active_rel.where(Tender.deadline_at >= now).order_by(Tender.deadline_at).limit(6)).scalars().all()
    events = db.execute(select(TenderEvent, Tender).join(Tender, Tender.id == TenderEvent.tender_id)
                        .where(TenderEvent.significant.is_(True), Tender.relevance_score >= min(th, 50))
                        .order_by(TenderEvent.observed_at.desc()).limit(10)).all()
    by_cat = dict(db.execute(select(Tender.best_category, func.count()).where(Tender.status == "active",
                                                                              Tender.relevance_score >= th)
                             .group_by(Tender.best_category)).all())
    sources = db.execute(select(Source).order_by(Source.id)).scalars().all()
    return render(request, "overview.html", db, kpi=kpi, latest=latest, closing=closing, events=events,
                  by_cat=by_cat, sources=sources, st=status_snapshot(db), threshold=th)


# --------------------------------------------------------------------------- tender explorer
SORTS = {"score": (Tender.relevance_score.desc(), Tender.deadline_at), "deadline": (Tender.deadline_at.asc(),),
         "published": (Tender.published_at.desc(),), "first_seen": (Tender.first_seen_at.desc(),),
         "value": (Tender.estimated_value.desc(),)}


def tender_query(db: Session, f: Dict):
    q = select(Tender)
    if f.get("q"):
        like = f"%{f['q'].strip()}%"
        q = q.where(or_(Tender.title.ilike(like), Tender.description.ilike(like), Tender.bid_number.ilike(like),
                        Tender.ra_number.ilike(like), Tender.organization.ilike(like), Tender.department.ilike(like)))
    if f.get("category"):
        q = q.where(Tender.best_category == f["category"])
    if f.get("min_score") not in (None, ""):
        q = q.where(Tender.relevance_score >= int(f["min_score"]))
    if f.get("status"):
        q = q.where(Tender.status == f["status"])
    if f.get("state"):
        q = q.where(Tender.state == f["state"])
    if f.get("source"):
        q = q.where(Tender.source_key == f["source"])
    if f.get("closing_days"):
        now = utcnow()
        q = q.where(Tender.deadline_at.between(now, now + timedelta(days=int(f["closing_days"]))))
    if f.get("watched"):
        q = q.where(Tender.watched.is_(True))
    if f.get("min_value"):
        q = q.where(Tender.estimated_value >= float(f["min_value"]))
    return q


@app.get("/tenders", response_class=HTMLResponse)
def tenders(request: Request, q: str = "", category: str = "", min_score: str = "50", status: str = "active",
            state: str = "", source: str = "", closing_days: str = "", watched: str = "", min_value: str = "",
            sort: str = "score", page: int = 1, db: Session = Depends(get_db), user: str = Depends(require_user)):
    f = dict(q=q, category=category, min_score=min_score, status=status, state=state, source=source,
             closing_days=closing_days, watched=watched, min_value=min_value)
    base = tender_query(db, f)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    per = 25
    page = max(1, page)
    rows = db.execute(base.order_by(*SORTS.get(sort, SORTS["score"])).offset((page - 1) * per).limit(per)).scalars().all()
    states = [s for (s,) in db.execute(select(Tender.state).where(Tender.state.is_not(None)).distinct().order_by(Tender.state))]
    sources = db.execute(select(Source).order_by(Source.id)).scalars().all()
    qs = {k: v for k, v in dict(f, sort=sort).items() if v}
    return render(request, "tenders.html", db, rows=rows, total=total, page=page, pages=max(1, -(-total // per)),
                  f=f, sort=sort, states=states, sources=sources, qs=qs, urlencode=urlencode)


@app.get("/tenders/{tid}", response_class=HTMLResponse)
def tender_detail(tid: int, request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    t = db.get(Tender, tid)
    if not t:
        raise HTTPException(404, "Tender not found")
    events = db.execute(select(TenderEvent).where(TenderEvent.tender_id == tid).order_by(TenderEvent.observed_at.desc())).scalars().all()
    obs = db.execute(select(BidObservation).where(BidObservation.tender_id == tid).order_by(BidObservation.observed_at.desc()).limit(20)).scalars().all()
    notes = db.execute(select(EmailNotification).where(EmailNotification.tender_id == tid).order_by(EmailNotification.created_at.desc())).scalars().all()
    src = db.execute(select(Source).where(Source.key == t.source_key)).scalar_one_or_none()
    matches = sorted(t.matches, key=lambda m: -m.score)
    return render(request, "tender_detail.html", db, t=t, d=t.details or {}, events=events, obs=obs, notes=notes,
                  src=src, matches=matches, links=nq.tender_links(t, S.public_base_url), threshold=threshold(db))


@app.post("/tenders/{tid}/watch")
def tender_watch(tid: int, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                 user: str = Depends(require_user)):
    auth.check_csrf(request, csrf)
    t = db.get(Tender, tid)
    if not t:
        raise HTTPException(404)
    t.watched = not t.watched
    flash(request, "Added to watchlist — changes will be tracked and alerted." if t.watched else "Removed from watchlist.")
    return back(f"/tenders/{tid}")


# --------------------------------------------------------------------------- monitoring centre
@app.get("/monitoring", response_class=HTMLResponse)
def monitoring(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    scans = db.execute(select(ScanExecution).order_by(ScanExecution.slot_at.desc(), ScanExecution.id.desc()).limit(60)).scalars().all()
    errors = db.execute(select(ScanError).order_by(ScanError.created_at.desc()).limit(40)).scalars().all()
    sysev = db.execute(select(SystemEvent).order_by(SystemEvent.created_at.desc()).limit(20)).scalars().all()
    sources = db.execute(select(Source).order_by(Source.id)).scalars().all()
    mail = db.execute(select(EmailNotification).order_by(EmailNotification.created_at.desc()).limit(40)).scalars().all()
    mail_counts = dict(db.execute(select(EmailNotification.status, func.count()).group_by(EmailNotification.status)).all())
    suppressed = db.scalar(select(func.count()).select_from(SuppressedNotification))
    return render(request, "monitoring.html", db, scans=scans, errors=errors, sysev=sysev, sources=sources,
                  mail=mail, mail_counts=mail_counts, suppressed=suppressed, st=status_snapshot(db),
                  helper=_helper_status(db), crawl=_crawl_status(db),
                  misses=db.scalar(select(func.count()).select_from(SystemEvent)
                                   .where(SystemEvent.event == "missed_bid_found")))


def _crawl_status(db: Session) -> dict:
    c = dict(cfg.get(db, "deep_crawl") or {})
    total = int(c.get("total_bids") or 0)
    pages = max(1, -(-total // 10)) if total else None
    page = int(c.get("page") or 1)
    c["pages_total"] = pages
    c["pct"] = min(100, round(100 * (page - 1) / pages)) if pages else 0
    for k in ("last_pass_finished", "updated", "paused_until", "pass_started"):
        if c.get(k):
            c[k] = ensure_utc(datetime.fromisoformat(c[k]))
    c["paused"] = bool(c.get("paused_until") and c["paused_until"] > utcnow())
    return c


def _helper_status(db: Session) -> dict:
    h = cfg.get(db, "helper") or {}
    seen = h.get("last_seen")
    if not seen:
        return {"configured": False}
    last = ensure_utc(datetime.fromisoformat(seen))
    return {"configured": True, "last_seen": last, "online": (utcnow() - last).total_seconds() < 180}


@app.post("/check-bid")
def check_bid_route(request: Request, bid: str = Form(""), csrf: str = Form(""), db: Session = Depends(get_db),
                    user: str = Depends(require_user)):
    """Look any GeM bid number up live: stores, scores and reads it, and logs it as a miss if it matched but the
    scans had not found it."""
    auth.check_csrf(request, csrf)
    from scanner.discovery.pipeline import check_bid
    try:
        res = check_bid(db, bid)
        db.commit()
    except Exception as e:
        db.rollback()
        flash(request, f"Could not check that bid right now ({type(e).__name__}). GeM may be slow; try again.", "err")
        return back("/tenders")
    flash(request, res["message"], "ok" if res.get("found") else "warn")
    return back(f"/tenders/{res['tender_id']}" if res.get("tender_id") else "/tenders")


@app.post("/monitoring/scan-now")
def scan_now(request: Request, csrf: str = Form(""), db: Session = Depends(get_db), user: str = Depends(require_user)):
    auth.check_csrf(request, csrf)
    from backend.jobs import trigger_manual_scan
    try:
        flash(request, trigger_manual_scan())
    except Exception as e:
        flash(request, f"Could not start a manual scan: {e}", "err")
    return back("/monitoring")


@app.get("/monitoring/notifications/{nid}", response_class=HTMLResponse)
def notification_detail(nid: int, request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    n = db.get(EmailNotification, nid)
    if not n:
        raise HTTPException(404)
    attempts = db.execute(select(EmailDeliveryAttempt).where(EmailDeliveryAttempt.notification_id == nid)
                          .order_by(EmailDeliveryAttempt.attempted_at)).scalars().all()
    return render(request, "notification.html", db, n=n, attempts=attempts)


# --------------------------------------------------------------------------- settings
@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    cats = db.execute(select(Category).order_by(Category.code)).scalars().all()
    sources = db.execute(select(Source).order_by(Source.id)).scalars().all()
    from scanner.source_adapters.registry import adapter_classes
    impl = {k: getattr(c, "implemented", True) for k, c in adapter_classes().items()}
    from core.geo import STATES
    return render(request, "settings.html", db, s=cfg.all_settings(db), cats=cats, sources=sources, impl=impl,
                  states=STATES, kind_labels=KIND_LABELS, dev_link=request.session.pop("dev_verify_link", None))


def _send_verification(request: Request, db: Session, email: str) -> None:
    token = secrets.token_urlsafe(32)
    rec = cfg.get(db, "recipient")
    rec.update(email=email, verified=False, verified_at=None,
               verification_token_hash=hashlib.sha256(token.encode()).hexdigest(),
               verification_sent_at=utcnow().isoformat())
    cfg.put(db, "recipient", rec)
    link = f"{S.public_base_url.rstrip('/')}/verify-email?token={token}"
    subject, body = nq.build_simple("Confirm this address for tender alerts",
                                    "Someone (hopefully you) asked OPvickerG BidRadar to send government tender alerts to "
                                    f"this address.\n\nConfirm within 48 hours:\n{link}\n\nIf you did not request this, ignore "
                                    "this email and no alerts will be sent.")
    n = nq.enqueue(db, f"verify:{hashlib.sha256(token.encode()).hexdigest()[:24]}", "verification", subject, body, None,
                   force_recipient=email)
    db.flush()
    if n is not None:
        nq.process_pending(db, sender=get_sender(), limit=5)
    if S.email_provider == "console":
        request.session["dev_verify_link"] = link     # dry-run mode only: nothing is emailed


@app.post("/settings/recipient")
def set_recipient(request: Request, email: str = Form(""), csrf: str = Form(""), db: Session = Depends(get_db),
                  user: str = Depends(require_user)):
    auth.check_csrf(request, csrf)
    email = email.strip()
    if not cfg.EMAIL_RX.match(email) or len(email) > 254:
        flash(request, "That does not look like a valid email address.", "err")
        return back("/settings#recipient")
    _send_verification(request, db, email)
    if S.email_provider == "console":
        flash(request, "Email provider is in dry-run (console) mode, so no email was sent. Use the verification link "
                       "shown below for local testing, or configure a provider first.", "warn")
    else:
        flash(request, f"Verification email queued for {email}. Alerts stay on hold until the link is clicked.")
    return back("/settings#recipient")


@app.post("/settings/recipient/resend")
def resend_verification(request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                        user: str = Depends(require_user)):
    auth.check_csrf(request, csrf)
    rec = cfg.get(db, "recipient")
    if not rec.get("email"):
        flash(request, "No recipient configured.", "err")
    elif rec.get("verified"):
        flash(request, "This address is already verified.")
    else:
        _send_verification(request, db, rec["email"])
        flash(request, "A new verification link was issued; the previous one no longer works.")
    return back("/settings#recipient")


@app.post("/settings/recipient/clear")
def clear_recipient(request: Request, csrf: str = Form(""), db: Session = Depends(get_db), user: str = Depends(require_user)):
    auth.check_csrf(request, csrf)
    cfg.put(db, "recipient", dict(cfg.DEFAULTS["recipient"]))
    for n in db.execute(select(EmailNotification).where(EmailNotification.status == "pending",
                                                        EmailNotification.event_type.not_in(["verification"]))).scalars():
        n.status, n.recipient = "held_no_recipient", None
    flash(request, "Recipient removed. New alerts will be held until a verified recipient is configured.")
    return back("/settings#recipient")


@app.get("/verify-email", response_class=HTMLResponse)
def verify_email(request: Request, token: str = "", db: Session = Depends(get_db)):
    rec = cfg.get(db, "recipient")
    ok = False
    if token and rec.get("verification_token_hash"):
        ok = hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), rec["verification_token_hash"])
        sent = rec.get("verification_sent_at")
        if ok and sent:
            from datetime import datetime
            ok = utcnow() - ensure_utc(datetime.fromisoformat(sent)) <= VERIFY_TTL
    if ok:
        rec.update(verified=True, verified_at=utcnow().isoformat(), verification_token_hash=None)
        cfg.put(db, "recipient", rec)
        released = nq.release_held(db)
        db.add(SystemEvent(level="info", event="recipient_verified", message="Alert recipient verified",
                           data={"released": released}))
    return render(request, "verify.html", db, ok=ok)


@app.post("/settings/test-email")
def test_email(request: Request, csrf: str = Form(""), db: Session = Depends(get_db), user: str = Depends(require_user)):
    auth.check_csrf(request, csrf)
    rec = cfg.get(db, "recipient")
    if not (rec.get("email") and rec.get("verified")):
        flash(request, "Configure and verify a recipient before sending a test notification.", "err")
        return back("/settings#recipient")
    t = db.execute(select(Tender).order_by(Tender.relevance_score.desc(), Tender.first_seen_at.desc()).limit(1)).scalar_one_or_none()
    if t is not None:
        subject, body, html = nq.render_tender_email(db, t, "new_tender")
        subject = subject.replace(nq.SUBJECT_PREFIX, nq.SUBJECT_PREFIX + " [TEST]")
        body = "THIS IS A TEST NOTIFICATION (layout preview using a stored tender).\n\n" + body
    else:
        subject, body = nq.build_simple("[TEST] Notification test", "This is a test notification. No tenders are stored yet.")
        html = None
    nq.enqueue(db, f"test:{utcnow().strftime('%Y%m%d%H%M%S')}", "test", subject, body, html, force_recipient=rec["email"])
    db.flush()
    stats = nq.process_pending(db, sender=get_sender(), limit=5)
    flash(request, f"Test notification processed via '{S.email_provider}' provider: {stats}. "
                   "'accepted' means the provider took it; 'delivered' appears only after the provider confirms delivery.")
    return back("/monitoring#mail")


@app.post("/settings/matching")
async def set_matching(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    form = await request.form()
    auth.check_csrf(request, form.get("csrf"))
    try:
        th = max(0, min(100, int(form.get("threshold", 75))))
    except ValueError:
        th = 75
    cats = [c for c in "ABCDEF" if form.get(f"cat_{c}")]
    old = cfg.get(db, "matching")
    cfg.put(db, "matching", {"threshold": th, "enabled_categories": cats})
    n = 0
    if set(cats) != set(old.get("enabled_categories") or []):
        from scanner.discovery.pipeline import rescore_all
        n = rescore_all(db)
    flash(request, f"Matching settings saved (alert threshold {th}, categories {', '.join(cats) or 'none'})."
                   + (f" Re-scored {n} stored tenders." if n else ""))
    return back("/settings#matching")


@app.post("/settings/keywords/add")
def add_keyword(request: Request, category: str = Form(...), kind: str = Form(...), phrase: str = Form(...),
                csrf: str = Form(""), db: Session = Depends(get_db), user: str = Depends(require_user)):
    auth.check_csrf(request, csrf)
    phrase = " ".join(phrase.strip().lower().split())[:120]
    cat = db.execute(select(Category).where(Category.code == category)).scalar_one_or_none()
    if not cat or kind not in KIND_LABELS or len(phrase) < 2:
        flash(request, "Invalid keyword.", "err")
        return back("/settings#keywords")
    if db.execute(select(Keyword).where(Keyword.category_id == cat.id, Keyword.phrase == phrase, Keyword.kind == kind)).scalar_one_or_none():
        flash(request, "That keyword already exists.", "warn")
        return back("/settings#keywords")
    db.add(Keyword(category_id=cat.id, phrase=phrase, kind=kind, weight=1.0, enabled=True, user_defined=True))
    db.flush()
    from scanner.discovery.pipeline import rescore_all
    n = rescore_all(db)
    flash(request, f"Added {KIND_LABELS[kind].lower()} '{phrase}' to category {category}. Re-scored {n} tenders.")
    return back(f"/settings#cat-{category}")


@app.post("/settings/keywords/{kid}/toggle")
def toggle_keyword(kid: int, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                   user: str = Depends(require_user)):
    auth.check_csrf(request, csrf)
    k = db.get(Keyword, kid)
    if not k:
        raise HTTPException(404)
    k.enabled = not k.enabled
    code = k.category.code
    db.flush()
    from scanner.discovery.pipeline import rescore_all
    rescore_all(db)
    flash(request, f"Keyword '{k.phrase}' {'enabled' if k.enabled else 'disabled'}.")
    return back(f"/settings#cat-{code}")


@app.post("/settings/keywords/{kid}/delete")
def delete_keyword(kid: int, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                   user: str = Depends(require_user)):
    auth.check_csrf(request, csrf)
    k = db.get(Keyword, kid)
    if not k:
        raise HTTPException(404)
    if not k.user_defined:
        flash(request, "Built-in keywords can be disabled but not deleted.", "warn")
        return back("/settings#keywords")
    code = k.category.code
    db.delete(k)
    db.flush()
    from scanner.discovery.pipeline import rescore_all
    rescore_all(db)
    flash(request, "Keyword deleted.")
    return back(f"/settings#cat-{code}")


@app.post("/settings/geography")
async def set_geography(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    form = await request.form()
    auth.check_csrf(request, form.get("csrf"))
    from core.geo import STATES
    states = [s for s in form.getlist("states") if s in STATES]
    pins = [p.strip() for p in str(form.get("pin_prefixes", "")).replace(";", ",").split(",") if p.strip().isdigit() and 1 <= len(p.strip()) <= 6]
    cfg.put(db, "geography", {"states": states, "pin_prefixes": pins})
    flash(request, "Geographic filter saved: " + (", ".join(states + pins) if (states or pins) else "all of India") + ".")
    return back("/settings#geography")


@app.post("/settings/sources/{key}/toggle")
def toggle_source(key: str, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                  user: str = Depends(require_user)):
    auth.check_csrf(request, csrf)
    from scanner.source_adapters.registry import adapter_classes
    src = db.execute(select(Source).where(Source.key == key)).scalar_one_or_none()
    cls = adapter_classes().get(key)
    if not src or not cls:
        raise HTTPException(404)
    if not getattr(cls, "implemented", True):
        flash(request, f"{src.name} cannot be enabled: {src.notes}", "warn")
        return back("/settings#sources")
    src.enabled = not src.enabled
    flash(request, f"{src.name} {'enabled' if src.enabled else 'disabled'}.")
    return back("/settings#sources")


@app.post("/settings/notifications")
async def set_notifications(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    form = await request.form()
    auth.check_csrf(request, form.get("csrf"))
    n = cfg.get(db, "notifications")
    for k in ("enabled", "new_tender", "deadline_changed", "corrigendum", "eligibility_changed", "cancelled", "outcome"):
        n[k] = bool(form.get(k))

    def _int(name, lo, hi, default):
        try:
            return max(lo, min(hi, int(form.get(name, default))))
        except ValueError:
            return default
    n["daily_summary"] = {"enabled": bool(form.get("daily_enabled")), "hour_ist": _int("daily_hour", 0, 23, 8)}
    n["weekly_summary"] = {"enabled": bool(form.get("weekly_enabled")), "weekday": _int("weekly_day", 0, 6, 0),
                           "hour_ist": _int("weekly_hour", 0, 23, 9)}
    cfg.put(db, "notifications", n)
    if n["enabled"]:
        nq.release_held(db)
    flash(request, "Notification preferences saved.")
    return back("/settings#notifications")


@app.post("/settings/monitoring")
def set_monitoring(request: Request, action: str = Form(...), reason: str = Form(""), csrf: str = Form(""),
                   db: Session = Depends(get_db), user: str = Depends(require_user)):
    auth.check_csrf(request, csrf)
    paused = action == "pause"
    cfg.put(db, "monitoring", {"paused": paused, "paused_reason": reason.strip()[:200] or None,
                               "paused_at": utcnow().isoformat() if paused else None})
    db.add(SystemEvent(level="warning" if paused else "info", event="monitoring_paused" if paused else "monitoring_resumed",
                       message=("Monitoring paused" + (f": {reason.strip()[:200]}" if reason.strip() else "")) if paused
                       else "Monitoring resumed", data={}))
    flash(request, "Monitoring paused — scheduled scans will be recorded as skipped until resumed." if paused
          else "Monitoring resumed — the next scheduled slot will scan normally.", "warn" if paused else "ok")
    return back("/settings#monitoring")


# --------------------------------------------------------------------------- webhooks & internal
@app.post("/webhooks/resend")
async def resend_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    hdrs = {k.lower(): v for k, v in request.headers.items()}
    if not verify_resend_webhook(body, hdrs, S.resend_webhook_secret):
        return PlainTextResponse("invalid signature", status_code=401)
    try:
        event = json.loads(body)
    except ValueError:
        return PlainTextResponse("bad payload", status_code=400)
    nq.apply_provider_event(db, event)
    return PlainTextResponse("ok")


@app.post("/internal/scan")
def internal_scan(request: Request):
    """Optional alternative trigger (e.g. an HTTP scheduler). Protected by a bearer token secret."""
    hdr = request.headers.get("authorization") or ""
    tok = hdr[7:].strip() if hdr.lower().startswith("bearer ") else ""
    if not S.internal_task_token or not tok or not hmac.compare_digest(tok, S.internal_task_token):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from scanner.scheduler.run import run_once
    res = run_once("scheduler")
    code = 500 if res.get("status") == "failed" else 200
    return JSONResponse({k: v for k, v in res.items() if k != "stats"}, status_code=code)


# --------------------------------------------------------------------------- JSON API
def tender_json(t: Tender, full: bool = False) -> Dict:
    d = {"id": t.id, "source": t.source_key, "bid_number": t.bid_number, "ra_number": t.ra_number, "title": t.title,
         "organization": t.organization, "department": t.department, "ministry": t.ministry, "state": t.state,
         "location": t.location, "status": t.status, "stage": t.stage, "relevance_score": t.relevance_score,
         "relevance_band": band(t.relevance_score), "best_category": t.best_category,
         "published_at": ensure_utc(t.published_at).isoformat() if t.published_at else None,
         "deadline_at": ensure_utc(t.deadline_at).isoformat() if t.deadline_at else None,
         "estimated_value": float(t.estimated_value) if t.estimated_value is not None else None,
         "emd": float(t.emd) if t.emd is not None else None, "contract_duration": t.contract_duration,
         "quantity": t.quantity, "official_url": t.official_url, "document_url": t.document_url,
         "watched": t.watched, "first_seen_at": ensure_utc(t.first_seen_at).isoformat(),
         "last_verified_at": ensure_utc(t.last_verified_at).isoformat() if t.last_verified_at else None}
    if full:
        det = dict(t.details or {})
        det.pop("doc_text", None)
        d.update(match_explanation=t.match_explanation, details=det,
                 matches=[{"category": m.category_code, "score": m.score, "matched": m.matched_phrases,
                           "explanation": m.explanation} for m in t.matches])
    return d


@app.get("/api/status")
def api_status(db: Session = Depends(get_db), user: str = Depends(require_user)):
    st = status_snapshot(db)
    last = st["last"]
    return {"monitoring_paused": cfg.get(db, "monitoring").get("paused"), "scan_running": st["running"],
            "next_slot_ist": fmt_ist(st["next_slot"]),
            "last_scan": {"slot_ist": fmt_ist(last.slot_at), "status": last.status, "stats": last.stats} if last else None,
            "last_24h": st["counts24"]}


@app.get("/api/tenders")
def api_tenders(q: str = "", category: str = "", min_score: str = "50", status: str = "", state: str = "",
                source: str = "", closing_days: str = "", sort: str = "score", limit: int = 50, offset: int = 0,
                db: Session = Depends(get_db), user: str = Depends(require_user)):
    base = tender_query(db, dict(q=q, category=category, min_score=min_score, status=status, state=state,
                                 source=source, closing_days=closing_days))
    rows = db.execute(base.order_by(*SORTS.get(sort, SORTS["score"])).offset(max(0, offset)).limit(min(200, max(1, limit)))).scalars().all()
    return {"count": len(rows), "items": [tender_json(t) for t in rows]}


@app.get("/api/tenders/{tid}")
def api_tender(tid: int, db: Session = Depends(get_db), user: str = Depends(require_user)):
    t = db.get(Tender, tid)
    if not t:
        raise HTTPException(404)
    return tender_json(t, full=True)


@app.get("/api/scans")
def api_scans(limit: int = 50, db: Session = Depends(get_db), user: str = Depends(require_user)):
    rows = db.execute(select(ScanExecution).order_by(ScanExecution.slot_at.desc()).limit(min(500, limit))).scalars().all()
    return [{"id": s.id, "slot_ist": fmt_ist(s.slot_at), "trigger": s.trigger, "status": s.status,
             "started_ist": fmt_ist(s.started_at), "finished_ist": fmt_ist(s.finished_at), "stats": s.stats,
             "error": (s.error or "")[:500] or None} for s in rows]


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/") or request.url.path.startswith("/webhooks/"):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    db = new_session()
    try:
        return render(request, "error.html", db, code=exc.status_code, detail=exc.detail, status_code=exc.status_code)
    finally:
        db.close()
