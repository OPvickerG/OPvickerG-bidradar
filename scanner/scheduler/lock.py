"""Lease-based distributed lock stored in the database (works identically on PostgreSQL and SQLite).

Acquire = one atomic conditional UPDATE (or INSERT for the first ever run). A crashed worker's lease simply
expires, so a stuck lock can never block scanning for longer than SCAN_LEASE_MINUTES."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.timeutil import utcnow
from database.schema.models import SchedulerLock

LOCK_NAME = "scan"


def acquire(db: Session, holder: str, lease_minutes: int, name: str = LOCK_NAME) -> bool:
    now = utcnow()
    exp = now + timedelta(minutes=lease_minutes)
    res = db.execute(update(SchedulerLock)
                     .where(SchedulerLock.name == name,
                            or_(SchedulerLock.expires_at.is_(None), SchedulerLock.expires_at < now,
                                SchedulerLock.holder == holder))
                     .values(holder=holder, acquired_at=now, expires_at=exp))
    if res.rowcount == 1:
        db.commit()
        return True
    if db.get(SchedulerLock, name) is not None:
        db.rollback()
        return False
    try:
        db.add(SchedulerLock(name=name, holder=holder, acquired_at=now, expires_at=exp))
        db.commit()
        return True
    except IntegrityError:            # another worker inserted it first
        db.rollback()
        return False


def heartbeat(db: Session, holder: str, lease_minutes: int, name: str = LOCK_NAME) -> bool:
    res = db.execute(update(SchedulerLock).where(SchedulerLock.name == name, SchedulerLock.holder == holder)
                     .values(expires_at=utcnow() + timedelta(minutes=lease_minutes)))
    db.commit()
    return res.rowcount == 1


def release(db: Session, holder: str, name: str = LOCK_NAME) -> None:
    db.execute(update(SchedulerLock).where(SchedulerLock.name == name, SchedulerLock.holder == holder)
               .values(holder=None, expires_at=None))
    db.commit()


def current(db: Session, name: str = LOCK_NAME) -> Optional[SchedulerLock]:
    return db.get(SchedulerLock, name)
