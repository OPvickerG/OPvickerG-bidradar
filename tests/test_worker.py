"""Always-on worker (India-hosted platforms): slot timing, crash resilience, health endpoint, clean stop."""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from scanner.scheduler import worker


def test_seconds_until_never_negative():
    now = datetime(2026, 11, 19, 4, 0, tzinfo=timezone.utc)
    assert worker.seconds_until(now + timedelta(minutes=5), now) == 300
    assert worker.seconds_until(now - timedelta(minutes=5), now) == 0


def test_crash_in_scan_does_not_kill_worker(monkeypatch):
    import scanner.scheduler.run as runmod

    def boom(*a, **k):
        raise RuntimeError("database unreachable")
    monkeypatch.setattr(runmod, "run_once", boom)
    assert worker.run_slot() is None
    assert worker.STATE["last_status"].startswith("crashed")


def test_loop_runs_each_slot_with_intended_minute_and_stops(monkeypatch):
    calls = []
    import scanner.scheduler.run as runmod

    def fake_run_once(trigger, scheduled_minute=None):
        calls.append((trigger, scheduled_minute))
        if len(calls) >= 2:
            worker._stop.set()
        return {"status": "succeeded"}
    monkeypatch.setattr(runmod, "run_once", fake_run_once)
    target = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=1)
    monkeypatch.setattr(worker, "next_slot", lambda now, interval=30: target)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    monkeypatch.delenv("PORT", raising=False)
    worker._stop.clear()
    t = threading.Thread(target=worker.main, daemon=True)
    t.start()
    t.join(timeout=10)
    worker._stop.clear()
    assert not t.is_alive()
    assert calls[0] == ("scheduler", None)                   # start-up catch-up of the current slot
    assert calls[1] == ("scheduler", target.minute)          # scheduled slot keeps its intended minute


def test_health_endpoint(monkeypatch):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    worker.STATE.update(last_status="succeeded")
    worker._serve_health(port)
    time.sleep(0.2)
    body = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read())
    assert body["status"] == "ok" and body["last_status"] == "succeeded"
