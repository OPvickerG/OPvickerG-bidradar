"""Always-on scan worker for India-hosted platforms (e.g. Voroa background worker).

GeM only accepts connections from Indian IP addresses, so the scanner must run on a host in India. Where that host
offers an always-on process instead of an external cron trigger, this worker is the scheduler:

  * wakes at every :00 and :30 IST (slots are UTC-aligned 30-minute boundaries; IST is UTC+5:30)
  * runs one scan per slot through run_once(), so the database lock, per-slot idempotency, missed-slot recovery,
    pause/resume and alerts all behave exactly as with Cloud Scheduler or GitHub Actions
  * on start-up it runs the current slot if it has not run yet (run_once is a no-op for a slot that already ran)
  * a crash inside a scan is logged and the loop continues; SIGTERM stops it cleanly between scans
  * if the platform requires a listening port (env PORT), a tiny health endpoint is served on it

Hybrid mode (SCAN_ROLE): a faster "helper" machine (e.g. the owner's Mac, when awake) scans each slot the moment it
opens and posts a heartbeat every minute. The always-on "server" sees the fresh heartbeat, gives the helper
HELPER_GRACE_MINUTES, then runs the slot only if it is still not done. If the helper is asleep, offline or stalls
mid-scan (its short lease stops being renewed), the server does the slot itself. Only the server sends email.

Run:  python -m scanner.scheduler.worker"""
from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
import traceback
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, Optional

from core.config import get_settings
from core.logs import log, setup_logging
from core.timeutil import ensure_utc, fmt_ist, next_slot, slot_for, utcnow

logger = logging.getLogger("bidradar.worker")
STATE: Dict = {"started": None, "last_run": None, "last_status": None, "next_slot": None}
_stop = threading.Event()


def seconds_until(target: datetime, now: Optional[datetime] = None) -> float:
    return max(0.0, (target - (now or utcnow())).total_seconds())


def run_slot(trigger: str = "scheduler", scheduled_minute: Optional[int] = None) -> Optional[Dict]:
    from scanner.scheduler.run import run_once
    try:
        res = run_once(trigger, scheduled_minute=scheduled_minute)
        STATE.update(last_run=fmt_ist(utcnow(), True), last_status=res.get("status"))
        return res
    except Exception as e:  # never let one bad scan kill the scheduler
        STATE.update(last_run=fmt_ist(utcnow(), True), last_status=f"crashed: {type(e).__name__}")
        log(logger, logging.ERROR, "scan crashed in worker", error=str(e)[:500], trace=traceback.format_exc()[-2000:])
        return None


class _Health(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"status": "ok", "service": "bidradar-scan-worker", **STATE}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep platform logs clean
        pass


def _serve_health(port: int) -> None:
    srv = HTTPServer(("0.0.0.0", port), _Health)
    threading.Thread(target=srv.serve_forever, daemon=True, name="health").start()
    log(logger, logging.INFO, "health endpoint listening", port=port)


HELPER_FRESH_S = 180


def helper_heartbeat() -> None:
    from backend import settings_service as cfg
    from database.session import session_scope
    with session_scope() as db:
        cfg.put(db, "helper", {"last_seen": utcnow().isoformat(), "last_run": STATE.get("last_run"),
                               "last_status": STATE.get("last_status")})


def helper_alive(now: Optional[datetime] = None) -> bool:
    """True when a helper machine has checked in within the last 3 minutes."""
    try:
        from backend import settings_service as cfg
        from database.session import session_scope
        with session_scope() as db:
            seen = (cfg.get(db, "helper") or {}).get("last_seen")
        if not seen:
            return False
        return ((now or utcnow()) - ensure_utc(datetime.fromisoformat(seen))).total_seconds() < HELPER_FRESH_S
    except Exception:
        return False


def heartbeat_loop(every_s: float = 60.0) -> None:
    while True:
        try:
            helper_heartbeat()
        except Exception as e:
            log(logger, logging.WARNING, "helper heartbeat failed", error=str(e)[:200])
        if _stop.wait(every_s):
            return


def mail_loop(every_s: float = 120.0) -> None:
    """Server only: send queued email (alerts queued by the helper included) every two minutes."""
    from database.session import session_scope
    from notifications.notification_queue import queue as nq
    while not _stop.wait(every_s):
        try:
            with session_scope() as db:
                st = nq.process_pending(db, limit=20)
            if any(st.values()):
                log(logger, logging.INFO, "email queue processed", **st)
        except Exception as e:
            log(logger, logging.WARNING, "email queue run failed", error=str(e)[:200])


def do_slot(slot: datetime, scheduled_minute: Optional[int]) -> Optional[Dict]:
    """Run one slot with hybrid hand-off: the server lets an awake helper go first; anyone who finds the lock
    busy retries each minute until the slot is done or its window closes."""
    s = get_settings()
    if s.scan_role != "helper" and helper_alive():
        grace_end = slot + timedelta(minutes=s.helper_grace_minutes)
        while not _stop.is_set() and seconds_until(grace_end) > 0:
            _stop.wait(min(30.0, seconds_until(grace_end)))
    window_end = slot + timedelta(minutes=s.scan_interval_minutes - 2)
    while True:
        res = run_slot(scheduled_minute=scheduled_minute)
        if res is not None and res.get("status") != "skipped_locked":
            return res
        if _stop.is_set() or utcnow() >= window_end:
            return res
        _stop.wait(60)


def keepalive_loop(url: str, every_s: float = 600.0) -> None:
    """Free web services sleep after ~15 idle minutes; a light request to our own health URL keeps the
    single always-on service (inside the platform's free monthly hours) awake so the scheduler keeps running."""
    import httpx
    while not _stop.wait(every_s):
        try:
            httpx.get(url, timeout=20)
        except Exception as e:
            log(logger, logging.WARNING, "keepalive ping failed", error=str(e)[:200])


def start_in_background(keepalive_url: Optional[str] = None) -> None:
    STATE["started"] = fmt_ist(utcnow(), True)
    threading.Thread(target=run_forever, daemon=True, name="scan-scheduler").start()
    if get_settings().send_email:
        threading.Thread(target=mail_loop, daemon=True, name="mail").start()
    if keepalive_url:
        threading.Thread(target=keepalive_loop, args=(keepalive_url,), daemon=True, name="keepalive").start()
    log(logger, logging.INFO, "in-app scan scheduler started", keepalive=bool(keepalive_url))


def run_forever() -> None:
    interval = get_settings().scan_interval_minutes
    last = slot_for(utcnow(), interval)
    do_slot(last, None)                          # catch up the current slot if it has not run yet
    while not _stop.is_set():
        current = slot_for(utcnow(), interval)
        if current > last:
            # a long run (start-up re-score, slow scan) or a sleeping laptop crossed a slot boundary: run it now
            last = current
            res = do_slot(current, current.minute)
            log(logger, logging.INFO, "slot finished (late start)", slot_ist=fmt_ist(current),
                status=(res or {}).get("status", "crashed"))
            continue
        target = next_slot(utcnow(), interval)
        STATE["next_slot"] = fmt_ist(target)
        log(logger, logging.INFO, "waiting for next slot", next_slot_ist=fmt_ist(target))
        while not _stop.is_set() and seconds_until(target) > 0:
            _stop.wait(min(60.0, seconds_until(target)))
        if _stop.is_set():
            break
        time.sleep(2)                            # land safely inside the new slot
        last = target
        res = do_slot(target, target.minute)
        log(logger, logging.INFO, "slot finished", slot_ist=fmt_ist(target),
            status=(res or {}).get("status", "crashed"))
    log(logger, logging.INFO, "scan worker stopping")


def main() -> int:
    setup_logging()
    STATE["started"] = fmt_ist(utcnow(), True)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, lambda *_: _stop.set())
        except ValueError:          # not the main thread (tests/embedding): rely on _stop being set directly
            pass
    if os.environ.get("PORT", "").isdigit():
        _serve_health(int(os.environ["PORT"]))
    s = get_settings()
    if s.scan_role == "helper":
        threading.Thread(target=heartbeat_loop, daemon=True, name="heartbeat").start()
    elif s.send_email:
        threading.Thread(target=mail_loop, daemon=True, name="mail").start()
    log(logger, logging.INFO, "scan worker started", role=s.scan_role, interval_minutes=s.scan_interval_minutes)
    run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
