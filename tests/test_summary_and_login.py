"""Plain-English bid summaries, and passwordless email sign-in (which also confirms the alert recipient)."""
from __future__ import annotations

import re
from datetime import timedelta

import pytest

from core.config import reset_settings
from core.timeutil import utcnow
from database.schema.models import EmailNotification, Tender
from database.session import session_scope
from scanner.bid_analysis.summary import clean_place, summarize, what


def _t(**kw):
    base = dict(source_key="gem_bidplus", external_id="S1", title="Repair and Overhauling Service - Diesel Generators- Dg Sets (DFM); CUMMINS; Yes; Buyer Premises",
                organization="Indian Army", ministry="Ministry of Defence", status="active", relevance_score=100,
                best_category="F", deadline_at=utcnow() + timedelta(days=3), details={})
    base.update(kw)
    return Tender(**base)


def test_summary_contains_key_facts_and_no_guesses():
    t = _t(estimated_value=850000, emd=17000, state="Delhi",
           details={"doc_fields": {"min_avg_turnover_inr": 1_000_000, "years_experience": "3 Year"}})
    s = summarize(t)
    for part in ("Repair & overhauling", "Cummins", "Indian Army", "Delhi", "₹8.50 L", "EMD ₹17,000",
                 "days left", "turnover ₹10.00 L/yr", "3 yrs experience", "Car & Generator Repair (100/100)"):
        assert part in s, (part, s)
    bare = summarize(_t(title="Repair of cars", details={}))
    assert "EMD" not in bare and "Est. value" not in bare and "Needs" not in bare      # unknown facts left out


def test_item_list_titles_are_condensed():
    assert what("Complete Overhauling for 70 KVA DG Set,Overhauling for 20 KVA DG Set,Overhauling for 5 KVA DG Set", None) \
        == "Complete Overhauling for 70 KVA DG Set (+2 more items)"
    assert clean_place("/Contact details of Grievance redressal HOD Email id :<email-redacted>") is None
    assert clean_place("Station Workshop Delhi Cantt") == "Station Workshop Delhi Cantt"


@pytest.fixture()
def app_client(db_url, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "Owner@Example.com")
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    reset_settings()
    import importlib
    import backend.app as appmod
    importlib.reload(appmod)
    from backend.auth import limiter
    limiter.fails.clear()
    from fastapi.testclient import TestClient
    return TestClient(appmod.app)


def _csrf(c):
    return re.search(r'name="csrf" value="([^"]+)"', c.get("/login").text).group(1)


def test_email_sign_in_link_logs_in_and_confirms_recipient(app_client):
    from backend import settings_service as cfg
    with session_scope() as db:
        cfg.put(db, "recipient", {"email": "owner@example.com", "verified": False})
    page = app_client.get("/login").text
    assert "Email me a sign-in link" in page
    r = app_client.post("/login/email", data={"email": "owner@example.com", "csrf": _csrf(app_client)})
    link = re.search(r'href="(http[^"]+/login/verify\?token=[^"]+)"', r.text)   # console provider: dev link shown
    assert link
    with session_scope() as db:
        sent = db.query(EmailNotification).filter_by(event_type="login_link").one()
        assert sent.recipient == "owner@example.com" and "/login/verify?token=" in sent.body_text
    r = app_client.get(link.group(1).replace("http://testserver", ""))
    assert r.status_code == 200 and "alerts are now switched on" in r.text
    with session_scope() as db:
        assert cfg.get(db, "recipient")["verified"] is True
    # link is single-use
    app_client.post("/logout", data={"csrf": re.search(r'name="csrf" value="([^"]+)"', app_client.get("/").text).group(1)})
    r = app_client.get(link.group(1).replace("http://testserver", ""))
    assert "invalid or has expired" in r.text


def test_unknown_email_gets_same_reply_and_no_mail(app_client):
    r = app_client.post("/login/email", data={"email": "stranger@example.com", "csrf": _csrf(app_client)})
    assert "If that address is allowed" in r.text
    with session_scope() as db:
        assert db.query(EmailNotification).filter_by(event_type="login_link").count() == 0
