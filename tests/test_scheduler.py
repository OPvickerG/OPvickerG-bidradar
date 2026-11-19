"""Requirement 12: scheduler execution — slots, locking, missed runs, pause, retries, exit codes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend import settings_service as cfg
from core.timeutil import fmt_ist, next_slot, slot_for, utcnow
from database.schema.models import ScanExecution, SchedulerLock
from database.session import session_scope
from scanner.scheduler import lock
from scanner.scheduler.run import main, run_once


def scans(db):
    return db.execute(select(ScanExecution).order_by(ScanExecution.slot_at, ScanExecution.id)).scalars().all()


def test_slots_are_every_30_minutes_on_the_hour_and_half_hour():
    t = datetime(2026, 11, 18, 4, 29, 59, tzinfo=timezone.utc)          # 09:59:59 IST
    assert fmt_ist(slot_for(t)) == "18 Nov 2026, 09:30 IST"
    assert fmt_ist(next_slot(t)) == "18 Nov 2026, 10:00 IST"
    assert fmt_ist(next_slot(t + timedelta(seconds=1))) == "18 Nov 2026, 10:30 IST"


def test_scheduled_run_records_its_slot_and_is_idempotent(db_url):
    now = slot_for(utcnow()) + timedelta(minutes=1)        # fixed offset so +2 min never crosses a slot
    r1 = run_once("scheduler", adapters={}, now=now, time_budget_s=30)
    r2 = run_once("scheduler", adapters={}, now=now + timedelta(minutes=2), time_budget_s=30)   # Cloud Scheduler retry
    assert r1["status"] == "succeeded" and r2["status"] == "already_ran"
    with session_scope() as db:
        rows = scans(db)
        assert len(rows) == 1 and rows[0].trigger == "scheduler"
        assert rows[0].slot_at.replace(tzinfo=timezone.utc) == slot_for(now)
        assert rows[0].finished_at is not None


def test_overlapping_run_is_skipped_while_lock_held(db_url):
    with session_scope() as db:
        assert lock.acquire(db, "other-worker", 25)
    r = run_once("scheduler", adapters={}, time_budget_s=30)
    assert r["status"] == "skipped_locked"
    with session_scope() as db:
        assert scans(db)[0].status == "skipped_locked"
        lock.release(db, "other-worker")
    r = run_once("scheduler", adapters={}, time_budget_s=30)        # same slot may run after a locked skip
    assert r["status"] == "succeeded"


def test_expired_lock_is_taken_over(db_url):
    with session_scope() as db:
        db.add(SchedulerLock(name="scan", holder="crashed-worker", acquired_at=utcnow() - timedelta(hours=1),
                             expires_at=utcnow() - timedelta(minutes=30)))
    assert run_once("scheduler", adapters={}, time_budget_s=30)["status"] == "succeeded"
    with session_scope() as db:
        assert lock.current(db).holder is None                    # released after the run


def test_lock_is_exclusive(db_url):
    with session_scope() as db:
        assert lock.acquire(db, "a", 25)
        assert not lock.acquire(db, "b", 25)
        assert lock.acquire(db, "a", 25)                          # re-entrant for the holder (heartbeat)
        assert lock.heartbeat(db, "a", 25)
        lock.release(db, "a")
        assert lock.acquire(db, "b", 25)


def test_missed_slots_are_recorded_and_next_run_catches_up(db_url):
    now = utcnow()
    with session_scope() as db:
        db.add(ScanExecution(slot_at=slot_for(now) - timedelta(hours=2), trigger="scheduler", status="succeeded",
                             stats={}))
    r = run_once("scheduler", adapters={}, now=now, time_budget_s=30)
    assert r["stats"]["missed_slots_recovered"] == 3
    with session_scope() as db:
        missed = [s for s in scans(db) if s.status == "missed"]
        assert len(missed) == 3 and all(s.trigger == "missed" for s in missed)


def test_paused_monitoring_skips_scheduled_but_allows_manual(db_url):
    with session_scope() as db:
        cfg.put(db, "monitoring", {"paused": True, "paused_reason": "holiday"})
    assert run_once("scheduler", adapters={}, time_budget_s=30)["status"] == "skipped_paused"
    assert run_once("manual", adapters={}, time_budget_s=30)["status"] == "succeeded"
    with session_scope() as db:
        st = {s.trigger: s for s in scans(db)}
        assert st["scheduler"].status == "skipped_paused" and "holiday" in st["scheduler"].error


def test_failed_slot_can_be_retried_and_exit_code_signals_failure(db_url, monkeypatch, gem):
    gem.fail_listing = True
    now = utcnow()
    r = run_once("scheduler", adapters={"gem_bidplus": gem.adapter()}, now=now, time_budget_s=30)
    assert r["status"] == "failed"
    gem.fail_listing = False
    r = run_once("scheduler", adapters={"gem_bidplus": gem.adapter()}, now=now, time_budget_s=30)
    assert r["status"] == "succeeded"
    with session_scope() as db:
        assert len(scans(db)) == 1 and scans(db)[0].status == "succeeded"

    import scanner.scheduler.run as runmod
    monkeypatch.setattr(runmod, "run_once", lambda trigger, **kw: {"status": "failed"})
    assert main(["--trigger", "scheduler"]) == 1
    monkeypatch.setattr(runmod, "run_once", lambda trigger, **kw: {"status": "succeeded"})
    assert main(["--trigger", "scheduler"]) == 0


def test_late_github_run_keeps_its_intended_slot(db_url):
    """GitHub Actions may start the :00 run at :37. It must count as the :00 slot, not create a 'missed' row."""
    from scanner.scheduler.run import intended_slot
    t = datetime(2026, 11, 19, 4, 37, tzinfo=timezone.utc)                     # 10:07 IST
    assert intended_slot(t, 0) == datetime(2026, 11, 19, 4, 0, tzinfo=timezone.utc)
    assert intended_slot(t, 30) == datetime(2026, 11, 19, 4, 30, tzinfo=timezone.utc)
    late = datetime(2026, 11, 19, 4, 22, tzinfo=timezone.utc)
    assert intended_slot(late, 30) == datetime(2026, 11, 19, 3, 30, tzinfo=timezone.utc)
    now = utcnow()
    with session_scope() as db:
        db.add(ScanExecution(slot_at=intended_slot(now, 0) - timedelta(minutes=30), trigger="scheduler",
                             status="succeeded", stats={}))
    r = run_once("scheduler", adapters={}, now=now, time_budget_s=30, scheduled_minute=intended_slot(now, 0).minute)
    assert r["status"] == "succeeded" and r["stats"]["missed_slots_recovered"] == 0
