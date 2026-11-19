"""Time helpers. Everything is STORED in UTC; everything is DISPLAYED in Asia/Kolkata."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover - fallback when tz database missing
    IST = timezone(timedelta(hours=5, minutes=30), "IST")

UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalise a datetime to aware UTC (SQLite returns naive datetimes; those are UTC by convention)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_ist(dt: Optional[datetime]) -> Optional[datetime]:
    dt = ensure_utc(dt)
    return dt.astimezone(IST) if dt else None


def fmt_ist(dt: Optional[datetime], with_seconds: bool = False) -> str:
    d = to_ist(dt)
    if not d:
        return "—"
    return d.strftime("%d %b %Y, %H:%M:%S IST" if with_seconds else "%d %b %Y, %H:%M IST")


def ist_wallclock_to_utc(naive_ist: datetime) -> datetime:
    """Interpret a naive datetime as Asia/Kolkata wall-clock time and return aware UTC."""
    return naive_ist.replace(tzinfo=IST).astimezone(UTC)


def parse_gem_listing_ts(value: Optional[str]) -> Optional[datetime]:
    """GeM's listing API stores IST wall-clock time with a misleading 'Z' suffix
    (verified in research Phase 1/2: the page renders it with timeZone 'UTC' to show IST).
    So '2026-11-18T11:00:00Z' means 11:00 IST."""
    if not value:
        return None
    try:
        naive = datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return ist_wallclock_to_utc(naive)


def parse_ist_string(value: Optional[str], formats=("%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y",
                                                     "%d-%B-%Y %I:%M:%S %p", "%d-%b-%Y %I:%M %p",
                                                     "%d/%m/%Y %H:%M:%S", "%d/%m/%Y")) -> Optional[datetime]:
    if not value:
        return None
    v = " ".join(value.split())
    for f in formats:
        try:
            return ist_wallclock_to_utc(datetime.strptime(v, f))
        except ValueError:
            continue
    return None


def slot_for(dt: datetime, interval_minutes: int = 30) -> datetime:
    """The scheduled slot (:00 or :30 in IST, which is also :00/:30 in UTC since IST = UTC+5:30)
    that `dt` belongs to."""
    dt = ensure_utc(dt)
    minute = (dt.minute // interval_minutes) * interval_minutes
    return dt.replace(minute=minute, second=0, microsecond=0)


def next_slot(dt: Optional[datetime] = None, interval_minutes: int = 30) -> datetime:
    dt = ensure_utc(dt or utcnow())
    return slot_for(dt, interval_minutes) + timedelta(minutes=interval_minutes)
