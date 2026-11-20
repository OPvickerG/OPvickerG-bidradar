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
