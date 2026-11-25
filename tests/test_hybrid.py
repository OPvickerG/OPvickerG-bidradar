"""Hybrid scanning: all-bids sweep, helper/server hand-off, stale-slot takeover, helper never sends email."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from backend import settings_service as cfg
from core.config import reset_settings
from core.timeutil import slot_for, utcnow
from database.schema.models import EmailNotification, ScanExecution, SystemEvent, Tender
from database.session import session_scope
from scanner.scheduler import worker
from tests.fixtures.synthetic import gem_bid_pdf, gem_doc


def test_sweep_scores_every_new_bid_but_stores_only_matches(gem, run_scan, monkeypatch):
    monkeypatch.setenv("SCANNER_SWEEP_PAGES", "3")
    reset_settings()
    gem.docs = [gem_doc(7100001, "Custom Bid for Services - Repair of vehicles"),
                gem_doc(7100002, "Supply of A4 paper"),
                gem_doc(7100003, "Office chair")]
    gem.pdfs["7100001"] = gem_bid_pdf("GEM/2026/B/7100001")
    res = run_scan({"gem_bidplus": gem.adapter()})
    assert res["stats"]["sweep"]["checked"] >= 2
    with session_scope() as db:
        titles = [t.title for t in db.execute(select(Tender)).scalars()]
        assert any("Repair of vehicles" in t for t in titles)
        assert not any("A4 paper" in t or "Office chair" in t for t in titles)      # scored, not stored
        assert {"7100002", "7100003"} <= set(cfg.get(db, "sweep_seen")["ids"])
    res2 = run_scan({"gem_bidplus": gem.adapter()})
    assert res2["stats"]["sweep"]["checked"] == 0                                   # remembered: not re-scored


def test_slot_left_running_by_a_silent_worker_is_taken_over(gem, run_scan):
    gem.docs = []
    slot = slot_for(utcnow(), 30)
    with session_scope() as db:
        db.add(ScanExecution(slot_at=slot, trigger="scheduler", status="running", started_at=slot,
                             worker_id="mac-helper:1:abc", stats={}))
    from scanner.scheduler.run import run_once
    res = run_once("scheduler", adapters={"gem_bidplus": gem.adapter()}, time_budget_s=60)
    assert res["status"] == "succeeded", res
    with session_scope() as db:
        ex = db.execute(select(ScanExecution).where(ScanExecution.slot_at == slot,
                                                    ScanExecution.trigger == "scheduler")).scalar_one()
        assert ex.status == "succeeded" and ex.worker_id != "mac-helper:1:abc"
        assert db.execute(select(SystemEvent).where(SystemEvent.event == "scan_taken_over")).scalar_one_or_none()


def test_helper_queues_but_never_sends_email(gem, run_scan, monkeypatch):
    with session_scope() as db:
        db.add(EmailNotification(dedupe_key="t:1", event_type="new_tender", subject="s", body_text="b",
                                 recipient="owner@example.com", status="pending"))
    monkeypatch.setenv("SEND_EMAIL", "false")
    reset_settings()
    gem.docs = []
    run_scan({"gem_bidplus": gem.adapter()})
    with session_scope() as db:
        assert db.execute(select(EmailNotification)).scalar_one().status == "pending"
    monkeypatch.setenv("SEND_EMAIL", "true")
    reset_settings()
    run_scan({"gem_bidplus": gem.adapter()})
    with session_scope() as db:
        assert db.execute(select(EmailNotification)).scalar_one().status == "accepted"


class _NoWait:
    def __init__(self):
        self.waits = []

    def is_set(self):
        return False

    def wait(self, s):
        self.waits.append(s)
        return False


def test_server_gives_an_awake_helper_a_head_start_then_retries_while_locked(monkeypatch):
    stop = _NoWait()
    monkeypatch.setattr(worker, "_stop", stop)
    monkeypatch.setattr(worker, "helper_alive", lambda now=None: True)
    clock = {"now": utcnow().replace(minute=0, second=5, microsecond=0)}
    slot = clock["now"].replace(second=0)
    monkeypatch.setattr(worker, "utcnow", lambda: clock["now"])
    real_wait = stop.wait

    def wait(s):                                   # time passes while the server waits
        clock["now"] += timedelta(seconds=s)
        return real_wait(s)
    stop.wait = wait
    results = [{"status": "skipped_locked"}, {"status": "already_ran"}]
    calls = []

    def fake_run_slot(trigger="scheduler", scheduled_minute=None):
        calls.append(clock["now"])
        return results.pop(0)
    monkeypatch.setattr(worker, "run_slot", fake_run_slot)
    res = worker.do_slot(slot, 0)
    assert res["status"] == "already_ran"
    assert calls[0] >= slot + timedelta(minutes=4)                   # grace period honoured
    assert len(calls) == 2                                           # retried once the lock was busy


def test_server_runs_immediately_when_no_helper(monkeypatch):
    monkeypatch.setattr(worker, "_stop", _NoWait())
    monkeypatch.setattr(worker, "helper_alive", lambda now=None: False)
    calls = []
    monkeypatch.setattr(worker, "run_slot", lambda trigger="scheduler", scheduled_minute=None:
                        calls.append(1) or {"status": "succeeded"})
    assert worker.do_slot(slot_for(utcnow(), 30), 0)["status"] == "succeeded"
    assert worker._stop.waits == [] and calls == [1]


def test_helper_heartbeat_makes_it_alive(db_url):
    assert worker.helper_alive() is False
    worker.helper_heartbeat()
    assert worker.helper_alive() is True
    assert worker.helper_alive(utcnow() + timedelta(minutes=5)) is False


# --- full-GeM deep crawl and "Check a bid" ----------------------------------------------------------------------
def test_deep_crawl_walks_every_page_of_gem_and_completes_a_pass(gem, run_scan, monkeypatch):
    monkeypatch.setenv("DEEP_CRAWL_MINUTES", "2")
    reset_settings()
    gem.page_size = 2
    gem.docs = [gem_doc(7200001, "Supply of A4 paper"), gem_doc(7200002, "Office chair"),
                gem_doc(7200003, "Custom Bid for Services - Repair of vehicles"),
                gem_doc(7200004, "Laptop Notebook"), gem_doc(7200005, "Annual maintenance of lifts")]
    gem.pdfs["7200003"] = gem_bid_pdf("GEM/2026/B/7200003")
    res = run_scan({"gem_bidplus": gem.adapter()})
    dc = res["stats"]["deep_crawl"]
    assert dc["listed"] >= 5 and dc["pages"] >= 3                     # every page read, end of list reached
    with session_scope() as db:
        st = cfg.get(db, "deep_crawl")
        assert st["passes"] == 1 and st["page"] == 1 and st["last_pass_checked"] >= 5 and st["total_bids"] == 5
        titles = [t.title for t in db.execute(select(Tender)).scalars()]
    assert any("Repair of vehicles" in t for t in titles)
    assert not any(x in t for t in titles for x in ("A4 paper", "Office chair", "Laptop"))


def test_deep_crawl_pauses_when_gem_refuses_and_keeps_its_place(gem, run_scan, monkeypatch):
    from scanner.source_adapters.base import SourceUnavailable
    monkeypatch.setenv("DEEP_CRAWL_MINUTES", "2")
    reset_settings()
    with session_scope() as db:
        cfg.put(db, "deep_crawl", {"page": 40, "total_bids": 45000})
    a = gem.adapter()

    def refuse(page):
        raise SourceUnavailable("connection refused")
    a.fetch_inventory = refuse
    gem.docs = []
    run_scan({"gem_bidplus": a})
    with session_scope() as db:
        st = cfg.get(db, "deep_crawl")
        assert st["page"] == 40 and st.get("paused_until") and "refused" in st["last_error"]
    calls = []
    a2 = gem.adapter()
    a2.fetch_inventory = lambda page: calls.append(page) or ([], 0)
    run_scan({"gem_bidplus": a2})
    assert calls == []                                                 # still paused: GeM is left alone


def test_check_a_bid_finds_stores_and_logs_a_miss(gem, db_url):
    from scanner.discovery.pipeline import check_bid
    from database.seed.loader import seed
    gem.docs = [gem_doc(7300001, "Custom Bid for Services - Repair of vehicles"),
                gem_doc(7300002, "Supply of A4 paper")]
    gem.pdfs["7300001"] = gem_bid_pdf("GEM/2026/B/7300001")
    with session_scope() as db:
        seed(db)
        r = check_bid(db, " gem/2026/b/7300001 ", adapters={"gem_bidplus": gem.adapter()})
    assert r["found"] and r["matches"] and not r["was_stored"] and "NOT been found" in r["message"]
    with session_scope() as db:
        assert db.get(Tender, r["tender_id"]).bid_number == "GEM/2026/B/7300001"
        assert db.execute(select(SystemEvent).where(SystemEvent.event == "missed_bid_found")).scalar_one()
        r2 = check_bid(db, "GEM/2026/B/7300001", adapters={"gem_bidplus": gem.adapter()})
    assert r2["was_stored"] and "NOT" not in r2["message"]                # already known: not a miss
    with session_scope() as db:
        low = check_bid(db, "GEM/2026/B/7300002", adapters={"gem_bidplus": gem.adapter()})
        assert low["found"] and not low["matches"] and "below your alert threshold" in low["message"]
        assert check_bid(db, "GEM/2026/B/9999999", adapters={"gem_bidplus": gem.adapter()})["found"] is False
        assert check_bid(db, "hello", adapters={"gem_bidplus": gem.adapter()})["ok"] is False


def test_long_scan_keeps_its_database_transaction_alive(gem, run_scan, monkeypatch):
    """Neon closes a transaction idle for 5 min; the scan must ping between slow GeM requests."""
    from scanner.discovery import pipeline
    pings = []
    real = pipeline.Scan._keepalive

    def spy(self):
        self._last_ping = -10**9                       # pretend a minute has passed every time
        before = self._last_ping
        real(self)
        pings.append(self._last_ping != before)
    monkeypatch.setattr(pipeline.Scan, "_keepalive", spy)
    gem.docs = [gem_doc(7400001, "Custom Bid for Services - Repair of vehicles")]
    gem.pdfs["7400001"] = gem_bid_pdf("GEM/2026/B/7400001")
    res = run_scan({"gem_bidplus": gem.adapter()})
    assert res["status"] in ("succeeded", "partial") and pings and all(pings)


def test_helper_that_cannot_reach_gem_leaves_the_slot_to_the_server(gem, run_scan, monkeypatch):
    from scanner.scheduler.run import run_once
    from scanner.source_adapters.base import SourceUnavailable
    gem.docs = []
    a = gem.adapter()

    def down(*args, **kw):
        raise SourceUnavailable("[Errno 60] Operation timed out")
    a.fetch_page = down
    a.fetch_latest = down
    monkeypatch.setenv("SCAN_ROLE", "helper")
    monkeypatch.setenv("SEND_EMAIL", "false")
    reset_settings()
    res = run_once("scheduler", adapters={"gem_bidplus": a}, time_budget_s=60)
    assert res["status"] == "failed"
    monkeypatch.setenv("SCAN_ROLE", "server")
    reset_settings()
    res2 = run_once("scheduler", adapters={"gem_bidplus": gem.adapter()}, time_budget_s=60)
    assert res2["status"] == "succeeded"                       # the server re-ran the same slot
    with session_scope() as db:
        assert len(db.execute(select(ScanExecution)).scalars().all()) == 1


# --- every bid delivering near home (GeM consignee-location search) --------------------------------------------
def test_local_sweep_reads_every_bid_delivering_near_home(gem, run_scan, monkeypatch):
    monkeypatch.setenv("LOCAL_SWEEP_MINUTES", "2")
    reset_settings()
    with session_scope() as db:
        from database.seed.loader import seed
        seed(db)
        cfg.put(db, "geography", {"states": [], "pin_prefixes": [],
                                  "home": {"name": "Testtown", "lat": 30.37, "lon": 76.8, "radius_km": 55}})
    gem.cities = {"HARYANA": ["AMBALA", "KARNAL", "SIRSA"], "CHANDIGARH": ["CHANDIGARH"]}
    ambala = [gem_doc(7300000 + i, f"Office item {i}") for i in range(12)]
    ambala.append(gem_doc(7300100, "Custom Bid for Services - Repair and servicing of staff car and bus"))
    gem.consignee = {("HARYANA", "AMBALA"): ambala,
                     ("CHANDIGARH", "CHANDIGARH"): [gem_doc(7300200, "Repair of DG set 125 KVA")],
                     ("HARYANA", "SIRSA"): [gem_doc(7300300, "Repair of vehicles at Sirsa")]}
    res = run_scan({"gem_bidplus": gem.adapter()})
    ls = res["stats"]["local"]
    assert ls["checked"] >= 14 and ls["cities"] >= 2
    assert not any("SIRSA" in c for c in gem.calls)              # Sirsa is ~200 km away: never asked for
    with session_scope() as db:
        rows = {t.external_id: t for t in db.execute(select(Tender)).scalars()}
        st = cfg.get(db, "local_sweep")
        assert [c[1] for c in st["cities"]] == ["AMBALA", "CHANDIGARH"]       # Karnal (~76 km) is outside 55+15
        assert st.get("passes", 0) >= 1
    car = next(t for t in rows.values() if "staff car" in t.title)
    assert car.details["consignees"] == [{"city": "AMBALA", "state": "HARYANA"}]
    assert car.details["geo"]["near"] == "yes" and car.details["geo"]["basis"] == "GeM consignee city"
    dg = next(t for t in rows.values() if "DG set" in t.title)
    assert dg.details["geo"]["place"] == "Chandigarh" and dg.details["geo"]["near"] == "yes"
    assert not any("Office item" in t.title for t in rows.values())        # unrelated bids are not stored
