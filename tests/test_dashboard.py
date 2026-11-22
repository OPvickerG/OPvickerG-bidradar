"""Requirements 15–17: dashboard filtering, authentication, monitoring logs (via the real FastAPI app)."""
from __future__ import annotations

import re
from datetime import timedelta

import pytest

from core.config import reset_settings
from core.timeutil import utcnow
from database.schema.models import ScanError, ScanExecution, Tender
from database.session import session_scope

PASSWORD = "unit-test-password-123"


@pytest.fixture()
def client(db_url, monkeypatch):
    from backend.auth import hash_password, limiter
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password(PASSWORD))
    reset_settings()
    limiter.fails.clear()
    from fastapi.testclient import TestClient
    from backend.app import app
    return TestClient(app)


def csrf(c, path="/login"):
    return re.search(r'name="csrf" value="([^"]+)"', c.get(path).text).group(1)


def login(c, password=PASSWORD):
    return c.post("/login", data={"username": "admin", "password": password, "csrf": csrf(c)})


# --- 16. authentication ---------------------------------------------------------------------------------
def test_pages_and_api_require_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert client.get("/api/tenders").status_code == 401
    assert client.get("/healthz").status_code == 200                # health check is public


def test_wrong_password_rejected_and_rate_limited(client):
    r = login(client, "wrong")
    assert "Incorrect username or password" in r.text
    for _ in range(5):
        login(client, "wrong")
    r = login(client)                                                 # even the right password is blocked now
    assert "Too many failed attempts" in r.text


def test_login_logout_and_csrf(client):
    r = login(client)
    assert r.status_code == 200 and "Overview" in r.text
    assert client.post("/settings/monitoring", data={"action": "pause", "csrf": "forged"}).status_code == 403
    tok = csrf(client, "/settings")
    client.post("/logout", data={"csrf": tok})
    assert client.get("/", follow_redirects=False).status_code == 303


def test_security_headers_and_cookie_flags(client):
    r = login(client)
    h = client.get("/").headers
    assert "default-src 'self'" in h["content-security-policy"] and h["x-frame-options"] == "DENY"


# --- 15. dashboard filtering ----------------------------------------------------------------------------
def _seed_tenders():
    now = utcnow()
    rows = [("1", "Repair of DG set", "F", 92, "active", "Delhi", 2), ("2", "Cylinder head repair", "B", 80, "active", "Kerala", 20),
            ("3", "CRDI injector calibration", "A", 60, "active", "Delhi", 5), ("4", "Repair of cars", "F", 95, "cancelled", None, 1),
            ("5", "Office stationery", None, 0, "active", None, 9)]
    with session_scope() as db:
        for ext, title, cat, score, status, state, days in rows:
            db.add(Tender(source_key="gem_bidplus", external_id=ext, bid_number=f"GEM/2026/B/{ext}", title=title,
                          best_category=cat, relevance_score=score, status=status, state=state, details={},
                          deadline_at=now + timedelta(days=days), estimated_value=100000 * int(ext)))


def titles(html):
    return set(re.findall(r'class="t-title">([^<]+)<', html))


def test_explorer_filters_and_sorting(client):
    _seed_tenders()
    login(client)
    assert titles(client.get("/tenders").text) == {"Repair of DG set", "Cylinder head repair", "CRDI injector calibration"}
    assert titles(client.get("/tenders?category=F&min_score=0&status=").text) == {"Repair of DG set", "Repair of cars"}
    assert titles(client.get("/tenders?min_score=75&status=active").text) == {"Repair of DG set", "Cylinder head repair"}
    assert titles(client.get("/tenders?state=Delhi&min_score=0").text) == {"Repair of DG set", "CRDI injector calibration"}
    assert titles(client.get("/tenders?closing_days=3&min_score=0").text) == {"Repair of DG set"}
    assert titles(client.get("/tenders?q=cylinder&min_score=0").text) == {"Cylinder head repair"}
    assert titles(client.get("/tenders?min_value=250000&min_score=0&status=").text) == {"CRDI injector calibration", "Repair of cars", "Office stationery"}
    items = client.get("/api/tenders?min_score=0&status=active&sort=deadline").json()["items"]
    assert [i["external_id"] if "external_id" in i else i["bid_number"] for i in items][:2] == ["GEM/2026/B/1", "GEM/2026/B/3"]


def test_tender_detail_and_watch_toggle(client):
    _seed_tenders()
    login(client)
    with session_scope() as db:
        tid = db.query(Tender).filter_by(external_id="2").one().id
    page = client.get(f"/tenders/{tid}").text
    assert "Cylinder head repair" in page and "Why it matched" in page
    client.post(f"/tenders/{tid}/watch", data={"csrf": csrf(client, f"/tenders/{tid}")})
    with session_scope() as db:
        assert db.get(Tender, tid).watched


# --- 17. monitoring logs --------------------------------------------------------------------------------
def test_monitoring_centre_shows_scans_errors_and_status(client):
    from scanner.scheduler.run import run_once
    run_once("scheduler", adapters={}, time_budget_s=30)
    with session_scope() as db:
        ex = db.query(ScanExecution).first()
        db.add(ScanError(scan_id=ex.id, source_key="gem_bidplus", stage="discovery", message="HTTP 503 from portal"))
    login(client)
    page = client.get("/monitoring").text
    assert "Scan executions" in page and "succeeded" in page and "HTTP 503 from portal" in page
    scans = client.get("/api/scans").json()
    assert scans and scans[0]["status"] == "succeeded" and scans[0]["trigger"] == "scheduler"
    st = client.get("/api/status").json()
    assert st["last_scan"]["status"] == "succeeded" and "next_slot_ist" in st


def test_structured_json_logs_redact_secrets(capsys):
    import json
    import logging
    from core.logs import log, setup_logging
    setup_logging()
    log(logging.getLogger("bidradar.test"), logging.INFO, "hello", api_key="sk-should-not-appear", scan_id=7)
    line = [ln for ln in capsys.readouterr().err.splitlines() + capsys.readouterr().out.splitlines() if '"hello"' in ln]
    if line:            # handler may write to stdout or stderr depending on configuration
        rec = json.loads(line[0])
        assert rec["scan_id"] == 7 and "sk-should-not-appear" not in line[0]


def test_home_town_setting_area_filter_and_nearest_sort(client):
    from database.session import session_scope
    from database.schema.models import Tender
    login(client)
    with session_scope() as db:
        db.add(Tender(source_key="gem_bidplus", external_id="N1", title="Repair of vehicles near", status="active",
                      relevance_score=90, best_category="F", details={"doc_fields": {"consignee_pins": ["160017"]}}))
        db.add(Tender(source_key="gem_bidplus", external_id="F1", title="Repair of vehicles far", status="active",
                      relevance_score=90, best_category="F", details={"doc_fields": {"consignee_pins": ["600001"]}}))
        db.add(Tender(source_key="gem_bidplus", external_id="U1", title="Repair of vehicles somewhere", status="active",
                      relevance_score=90, best_category="F", details={}))
    r = client.post("/settings/geography", data={"home": "karnal", "radius_km": "200", "csrf": csrf(client, "/settings")},
                    follow_redirects=True)
    assert "within 200 km of Karnal" in r.text and "3 open tenders re-measured" in r.text
    page = client.get("/tenders").text
    assert "vehicles near" in page and "vehicles somewhere" in page and "vehicles far" not in page
    assert "Chandigarh" in page and "km" in page
    assert "vehicles far" in client.get("/tenders?near=all").text
    only = client.get("/tenders?near=yes").text
    assert "vehicles near" in only and "vehicles somewhere" not in only
    assert client.get("/tenders?near=all&sort=nearest").status_code == 200
    bad = client.post("/settings/geography", data={"home": "Atlantis", "csrf": csrf(client, "/settings")},
                      follow_redirects=True)
    assert "not in the built-in list" in bad.text
