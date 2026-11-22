"""Scan entrypoint executed by the cloud scheduler (Cloud Run Job, triggered by Cloud Scheduler at :00 and :30
Asia/Kolkata). Also usable for a manual run:  python -m scanner.scheduler.run --trigger manual

Guarantees:
  * one scan at a time (DB lease lock) — overlapping triggers are recorded as skipped_locked
  * each slot is recorded once per trigger type (unique slot_at+trigger); a failed slot may be retried
  * slots with no execution since the last run are recorded as 'missed' and the next run pages deeper
  * paused monitoring is recorded as skipped_paused (visible in the Monitoring Centre)
  * exit code != 0 on failure so Cloud Run retries the task"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import traceback
import uuid
from datetime import timedelta
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import settings_service as cfg
from core.config import get_settings
from core.logs import log, setup_logging
from core.timeutil import ensure_utc, fmt_ist, slot_for, utcnow
from database.schema.models import ScanExecution, SystemEvent
from database.seed.loader import seed
from database.session import session_scope
from scanner.discovery.pipeline import Scan
from scanner.scheduler import lock

logger = logging.getLogger("bidradar.scheduler")
MAX_MISSED_BACKFILL = 48          # never create more than a day of 'missed' rows in one go


class _LostLock(Exception):
    pass


def worker_id() -> str:
    name = os.environ.get("WORKER_NAME") or os.environ.get("CLOUD_RUN_EXECUTION") or socket.gethostname()
    return f"{name}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def record_missed(db: Session, slot) -> int:
    """Create 'missed' rows for every :00/:30 slot between the last recorded slot and this one."""
    interval = timedelta(minutes=get_settings().scan_interval_minutes)
    last = db.execute(select(ScanExecution.slot_at).where(ScanExecution.trigger.in_(["scheduler", "missed"]))
                      .order_by(ScanExecution.slot_at.desc()).limit(1)).scalar_one_or_none()
    if last is None:
        return 0
    last = ensure_utc(last)
    gaps, s = [], last + interval
    while s < slot and len(gaps) < MAX_MISSED_BACKFILL:
        gaps.append(s)
        s += interval
    for g in gaps:
        db.add(ScanExecution(slot_at=g, trigger="missed", status="missed", started_at=None, finished_at=None,
                             stats={}, error="No scan execution was recorded for this slot (scheduler or job did not run)."))
    if gaps:
        db.add(SystemEvent(level="warning", event="missed_scans",
                           message=f"{len(gaps)} scheduled scan slot(s) missed between {fmt_ist(gaps[0])} and {fmt_ist(gaps[-1])}",
                           data={"count": len(gaps)}))
    db.flush()
    return len(gaps)


def _claim_execution(db: Session, slot, trigger: str, wid: str, holding_lock: bool = False) -> Optional[ScanExecution]:
    ex = db.execute(select(ScanExecution).where(ScanExecution.slot_at == slot, ScanExecution.trigger == trigger)
                    ).scalar_one_or_none()
    if ex is not None:
        # We hold the lock, so a slot still marked 'running' belongs to a worker that went silent (a laptop that
        # slept mid-scan, a restarted container). Its lease has run out: take the slot over and finish it.
        abandoned = holding_lock and ex.status == "running" and ex.worker_id != wid
        if abandoned:
            db.add(SystemEvent(level="warning", event="scan_taken_over",
                               message=f"slot {fmt_ist(slot)} abandoned by {ex.worker_id}; re-run by {wid}",
                               data={"scan_id": ex.id}))
        if abandoned or ex.status in ("failed", "skipped_locked") or (trigger == "manual"):
            ex.status, ex.started_at, ex.finished_at, ex.error, ex.worker_id, ex.stats = "running", utcnow(), None, None, wid, {}
            db.flush()
            return ex
        return None                    # this slot already ran (or is running) — Cloud Scheduler retry / duplicate
    ex = ScanExecution(slot_at=slot, trigger=trigger, status="running", started_at=utcnow(), worker_id=wid, stats={})
    try:
        with db.begin_nested():
            db.add(ex)
    except IntegrityError:
        return None
    return ex


def intended_slot(now, scheduled_minute: int):
    """Hosted schedulers (GitHub Actions in particular) can start a run many minutes late. When the trigger
    says which slot it was for (minute 0 or 30), use the most recent such time instead of the wall clock."""
    cand = now.replace(minute=scheduled_minute, second=0, microsecond=0)
    return cand if cand <= now else cand - timedelta(hours=1)


def run_once(trigger: str = "scheduler", adapters=None, sender=None, now=None,
             time_budget_s: Optional[float] = None, scheduled_minute: Optional[int] = None) -> Dict:
    s = get_settings()
    now = now or utcnow()
    slot = slot_for(now, s.scan_interval_minutes)
    if trigger == "scheduler" and scheduled_minute is not None:
        slot = intended_slot(now, scheduled_minute)
    if trigger == "manual":
        slot = now.replace(microsecond=0)     # manual runs get their own identity
    wid = worker_id()
    result: Dict = {"slot": fmt_ist(slot), "trigger": trigger, "worker": wid}

    with session_scope() as db:
        if not lock.acquire(db, wid, s.scan_lease_minutes):
            holder = lock.current(db)
            ex = _claim_execution(db, slot, trigger, wid)
            if ex is not None:
                ex.status, ex.finished_at = "skipped_locked", utcnow()
                ex.error = f"another scan holds the lock until {fmt_ist(holder.expires_at) if holder else '?'}"
            db.commit()
            result["status"] = "skipped_locked"
            log(logger, logging.WARNING, "scan skipped: lock held", **result)
            return result
        try:
            seed(db)                       # idempotent; runs under the lock so workers never race
            from scanner.discovery.pipeline import rescore_if_rules_changed
            rescore_if_rules_changed(db)   # no-op unless the matching rules changed since the last scan
            from scanner.discovery.pipeline import relocate_if_changed
            relocate_if_changed(db)        # no-op unless the location rules or home place changed
            missed = record_missed(db, slot) if trigger == "scheduler" else 0
            ex = _claim_execution(db, slot, trigger, wid, holding_lock=True)
            if ex is None:
                db.commit()
                lock.release(db, wid)
                result["status"] = "already_ran"
                log(logger, logging.INFO, "slot already processed", **result)
                return result
            mon = cfg.get(db, "monitoring")
            if mon.get("paused") and trigger != "manual":
                ex.status, ex.finished_at = "skipped_paused", utcnow()
                ex.error = f"Monitoring paused{': ' + mon['paused_reason'] if mon.get('paused_reason') else ''}"
                db.commit()
                lock.release(db, wid)
                result["status"] = "skipped_paused"
                return result
            db.commit()
            ex_id = ex.id
        except Exception:
            db.rollback()
            lock.release(db, wid)
            raise

    status, err, stats = "failed", None, {}
    lost, done = threading.Event(), threading.Event()

    def renew_lease():                 # keep the lease short but alive while this scan is genuinely running
        while not done.wait(60):
            try:
                with session_scope() as hb:
                    if not lock.heartbeat(hb, wid, s.scan_lease_minutes):
                        lost.set()
                        return
            except Exception as e:
                log(logger, logging.WARNING, "lease renewal failed", error=str(e)[:200])

    threading.Thread(target=renew_lease, daemon=True, name="lease").start()
    try:
        with session_scope() as db:
            ex = db.get(ScanExecution, ex_id)
            scan = Scan(db, ex, adapters=adapters, sender=sender, catch_up=missed > 0, time_budget_s=time_budget_s)
            scan.should_stop = lost.is_set
            stats = scan.run()
            if lost.is_set():          # another worker owns this slot now: keep saved tenders, leave its row alone
                db.commit()
                status, err = "lost_lock", "lease expired during the scan (machine paused?); another worker took over"
                raise _LostLock()
            stats["missed_slots_recovered"] = missed
            srcs = stats.get("sources", {})
            failed_sources = [k for k, v in srcs.items() if v.get("status") == "failed"]
            worked = [k for k, v in srcs.items() if v.get("pages", 0) > 0]
            if failed_sources and not worked:
                status = "failed"
            elif failed_sources or stats.get("errors"):
                status = "partial"
            else:
                status = "succeeded"
            if status == "failed":
                err = "no source could be scanned: " + ", ".join(failed_sources)
            elif failed_sources and s.scan_role == "helper":
                # the helper machine could not reach a portal (seen 22 Nov 2026: GeM timed out from the Mac): mark
                # the slot failed so the always-on server, which retries failed slots, scans it in full
                status = "failed"
                err = "helper could not reach " + ", ".join(failed_sources) + "; the server will scan this slot"
            ex.status, ex.finished_at, ex.stats, ex.error = status, utcnow(), stats, err
            db.commit()
    except _LostLock:
        log(logger, logging.WARNING, "scan stopped: lock lost", scan_id=ex_id)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        log(logger, logging.ERROR, "scan crashed", error=err, trace=traceback.format_exc()[-2000:])
        with session_scope() as db:
            ex = db.get(ScanExecution, ex_id)
            ex.status, ex.finished_at, ex.error = "failed", utcnow(), (err + "\n" + traceback.format_exc())[-4000:]
            ex.stats = stats
            db.add(SystemEvent(level="error", event="scan_failed", message=err[:500], data={"scan_id": ex_id}))
            db.commit()
    finally:
        done.set()
        with session_scope() as db:
            lock.release(db, wid)
    result.update(status=status, error=err, stats=stats, scan_id=ex_id)
    log(logger, logging.INFO if status != "failed" else logging.ERROR, "scan finished", **{k: v for k, v in result.items() if k != "stats"},
        new=stats.get("new_tenders"), relevant_new=stats.get("relevant_new"), errors=stats.get("errors"))
    return result


def main(argv=None) -> int:
    setup_logging()
    p = argparse.ArgumentParser(description="OPvickerG BidRadar scan")
    p.add_argument("--trigger", default=os.environ.get("SCAN_TRIGGER", "scheduler"),
                   choices=["scheduler", "manual", "recovery"])
    p.add_argument("--scheduled-minute", type=int, choices=[0, 30],
                   default=int(os.environ["SCHEDULED_MINUTE"]) if os.environ.get("SCHEDULED_MINUTE", "").isdigit() else None,
                   help="slot minute the scheduler fired for (lets a late-starting run keep its slot)")
    a = p.parse_args(argv)
    res = run_once(a.trigger, scheduled_minute=a.scheduled_minute)
    return 1 if res.get("status") == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
