"""Change detection: compare what a portal shows now with what we stored, emit timestamped events.

Only meaningful differences produce events; whitespace / case / punctuation-only edits are ignored."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, List, Optional

from core.timeutil import ensure_utc, fmt_ist
from scanner.keyword_engine.text import normalise

FINAL_STATUSES = {"cancelled"}

# event_type -> notification preference key (None = record only)
EVENT_TO_PREF = {"deadline_changed": "deadline_changed", "corrigendum_published": "corrigendum",
                 "eligibility_changed": "eligibility_changed", "cancelled": "cancelled",
                 "outcome_available": "outcome", "quantity_changed": "corrigendum",
                 "value_changed": "corrigendum", "specification_changed": "corrigendum"}

ELIGIBILITY_FIELDS = ("min_avg_turnover", "oem_avg_turnover", "years_experience", "past_performance",
                      "documents_required", "emd_amount", "epbg_percent", "mse_preference", "mii_preference")


@dataclass
class Change:
    event_type: str
    field: Optional[str]
    old: Optional[str]
    new: Optional[str]
    significant: bool = True


def _norm(v) -> str:
    return normalise(str(v)) if v is not None else ""


def state_fingerprint(state: Dict) -> str:
    keys = ("title", "description", "deadline_at", "status", "quantity", "estimated_value", "corrigenda_count",
            "outcome_ranks")
    canon = {k: (_norm(state.get(k)) if k in ("title", "description") else
                 str(_ranks(state.get(k))) if k == "outcome_ranks" else str(state.get(k))) for k in keys}
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()


def diff_listing(old: Dict, new: Dict) -> List[Change]:
    """old/new: dicts with title, description, deadline_at (aware dt), status, quantity, estimated_value."""
    out: List[Change] = []
    od, nd = ensure_utc(old.get("deadline_at")), ensure_utc(new.get("deadline_at"))
    if nd and od and abs((nd - od).total_seconds()) >= 60:
        out.append(Change("deadline_changed", "deadline_at", fmt_ist(od), fmt_ist(nd), True))
    elif nd and not od:
        out.append(Change("deadline_changed", "deadline_at", None, fmt_ist(nd), False))
    os_, ns = old.get("status"), new.get("status")
    if ns and os_ != ns:
        if ns == "cancelled":
            out.append(Change("cancelled", "status", os_, ns, True))
        else:
            out.append(Change("status_changed", "status", os_, ns, False))
    for f, ev in (("quantity", "quantity_changed"), ("estimated_value", "value_changed")):
        o, n = old.get(f), new.get(f)
        if o is not None and n is not None:
            try:
                if abs(float(o) - float(n)) > 0.0001:
                    out.append(Change(ev, f, str(o), str(n), True))
            except (TypeError, ValueError):
                pass
    for f in ("title", "description"):
        o, n = _norm(old.get(f)), _norm(new.get(f))
        if o and n and o != n:
            out.append(Change("specification_changed", f, (old.get(f) or "")[:300], (new.get(f) or "")[:300],
                              significant=_meaningful_text_change(o, n)))
    return out


def _meaningful_text_change(o: str, n: str) -> bool:
    from rapidfuzz import fuzz
    return fuzz.token_set_ratio(o, n) < 92


def diff_corrigenda(old_count: int, corrigenda: List[Dict]) -> List[Change]:
    if len(corrigenda) <= old_count:
        return []
    new_items = corrigenda[: len(corrigenda) - old_count]     # GeM lists newest first
    desc = "; ".join(f"{c.get('modified_on')}: {c.get('change')}" for c in new_items[:5])
    return [Change("corrigendum_published", "corrigenda", str(old_count), desc, True)]


def diff_eligibility(old_fields: Dict, new_fields: Dict) -> List[Change]:
    out = []
    for f in ELIGIBILITY_FIELDS:
        o, n = old_fields.get(f), new_fields.get(f)
        if o is not None and n is not None and _norm(o) != _norm(n):
            out.append(Change("eligibility_changed", f, str(o), str(n), True))
    return out


def _ranks(r) -> list:
    return [list(x) for x in (r or [])]


def diff_outcome(old_outcome: Optional[Dict], new_outcome: Optional[Dict]) -> List[Change]:
    if not new_outcome or not new_outcome.get("ranks"):
        return []
    if old_outcome and _ranks(old_outcome.get("ranks")) == _ranks(new_outcome.get("ranks")) and \
            old_outcome.get("l1_name") == new_outcome.get("l1_name"):
        return []
    ranks = new_outcome["ranks"]
    summary = ", ".join(f"{r}: ₹{p:,.0f}" for r, p in ranks[:3] if p) + \
        (f"; L1 published: {new_outcome['l1_name']}" if new_outcome.get("l1_name") else "")
    return [Change("outcome_available", "outcome", None if not old_outcome else str(old_outcome.get("ranks")),
                   summary, True)]


def deadline_passed_recently(deadline_at, now, window_minutes: int = 120) -> bool:
    d = ensure_utc(deadline_at)
    return bool(d and timedelta(0) <= now - d <= timedelta(minutes=window_minutes))
