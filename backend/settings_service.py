"""Application settings stored in the database (editable from the dashboard)."""
from __future__ import annotations

import copy
import re
from typing import Any, Dict

from sqlalchemy.orm import Session

from core.timeutil import utcnow
from database.schema.models import AppSetting

DEFAULTS: Dict[str, Any] = {
    "monitoring": {"paused": False, "paused_reason": None, "paused_at": None},
    "recipient": {"email": None, "verified": False, "verification_token_hash": None, "verification_sent_at": None},
    "matching": {"threshold": 75, "enabled_categories": ["A", "B", "C", "D", "E", "F"]},
    "geography": {"states": [], "pin_prefixes": []},     # empty = all India
    "notifications": {
        "enabled": True,
        "new_tender": True, "deadline_changed": True, "corrigendum": True, "eligibility_changed": True,
        "cancelled": True, "outcome": True,
        "daily_summary": {"enabled": False, "hour_ist": 8},
        "weekly_summary": {"enabled": False, "weekday": 0, "hour_ist": 9},   # 0 = Monday
    },
    "sources": {},      # source_key -> {"enabled": bool}
}

EMAIL_RX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def get(db: Session, key: str) -> Any:
    row = db.get(AppSetting, key)
    base = copy.deepcopy(DEFAULTS.get(key, {}))
    if row is None:
        return base
    if isinstance(base, dict) and isinstance(row.value, dict):
        merged = base
        for k, v in row.value.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = {**merged[k], **v}
            else:
                merged[k] = v
        return merged
    return row.value


def put(db: Session, key: str, value: Any) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value, updated_at=utcnow()))
    else:
        row.value = value
        row.updated_at = utcnow()
    db.flush()


def all_settings(db: Session) -> Dict[str, Any]:
    return {k: get(db, k) for k in DEFAULTS}
