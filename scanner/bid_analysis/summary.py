"""One-paragraph, plain-English summary of a tender, built only from facts the portal or the tender document
states. Missing facts are simply left out; nothing is guessed.

Example:
  "Repair & overhauling of diesel generators (DG sets) · make: Cummins. Buyer: Indian Army, Ministry of Defence —
   Delhi. Est. value ₹8.50 L · EMD ₹17,000. Closes 21 Nov 2026, 1:00 PM IST (2 days left). Needs: turnover
   ₹10 L/yr, 3 yrs experience. Matches Car & Generator Repair (100/100)."
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from core.timeutil import ensure_utc, to_ist, utcnow

CATEGORY_NAMES = {"A": "CRDI & Diesel Injection", "B": "Cylinder Head Work", "C": "Engine Machining",
                  "D": "Automotive Scanning & Diagnostics", "E": "Plates Facing & Grinding",
                  "F": "Car & Generator Repair"}
_NOISE = {"yes", "no", "buyer premises", "service provider", "service provider premises", "na", "n/a", "-", "others"}
_CODE = re.compile(r"^[A-Z]{0,5}\d{5,}[A-Z0-9-]*\s*")


def money(v) -> Optional[str]:
    if v in (None, "", 0):
        return None
    v = float(v)
    if v >= 1e7:
        return f"₹{v / 1e7:.2f} Cr"
    if v >= 1e5:
        return f"₹{v / 1e5:.2f} L"
    return f"₹{v:,.0f}"


def clean_place(s: Optional[str]) -> Optional[str]:
    """Drop location values that are really contact boilerplate from the PDF."""
    if not s:
        return None
    bad = ("email", "contact", "redress", "<", "@", "http", "phone")
    s = s.strip(" /")
    return None if (len(s) > 90 or any(b in s.lower() for b in bad)) else s


def _cut(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"


def _tidy(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip(" ,;:-")
    s = re.sub(r"(?i)repair and overhauling service", "Repair & overhauling", s).replace(" - ", ": ", 1)
    letters = [c for c in s if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7:     # ALL CAPS -> Sentence case
        s = s.lower()
        s = re.sub(r"\b(dg|kva|obd|ecu|crdi|fip|mfm|ac|lmv|hmv|tata|ii)\b", lambda m: m.group(1).upper(), s)
    return s[:1].upper() + s[1:] if s else s


def _items(text: Optional[str]) -> List[str]:
    return [i.strip() for i in (text or "").split(",") if i.strip()]


def what(title: str, description: Optional[str]) -> str:
    """Short description of what is being procured (first item + how many more)."""
    t_items = _items(title)
    if len(t_items) >= 2 and ";" not in (title or ""):  # the title itself is an item list
        first = _tidy(_CODE.sub("", t_items[0]))
        d_items = _items(description)
        n = max(len(t_items), len(d_items))
        return _cut(first, 90) + (f" (+{n - 1} more items)" if n > 1 else "")
    parts = [p.strip() for p in (title or "").split(";") if p.strip()]
    head = _tidy(parts[0]) if parts else ""
    extras = [p for p in parts[1:] if p.lower() not in _NOISE and not p.isdigit() and len(p) <= 60][:1]
    text = head + (f" · {_tidy(extras[0])}" if extras else "")
    d_items = _items(description)
    if len(d_items) >= 3 and (description or "").strip() != (title or "").strip():
        names = [_cut(_tidy(_CODE.sub("", i)), 40) for i in d_items[:2]]
        text += f" — {len(d_items)} items incl. " + "; ".join(n for n in names if n)
    return _cut(text, 220)


def deadline_text(deadline_at) -> Optional[str]:
    d = ensure_utc(deadline_at)
    if not d:
        return None
    ist = to_ist(d)
    left = d - utcnow()
    hrs = left.total_seconds() / 3600
    when = ist.strftime("%d %b %Y, %I:%M %p IST").replace(" 0", " ")
    if hrs < 0:
        return f"Closed {when}"
    rel = f"{int(hrs)} h left" if hrs < 48 else f"{int(hrs // 24)} days left"
    return f"Closes {when} ({rel})"


def requirements(f: Dict) -> List[str]:
    out = []
    if f.get("min_avg_turnover_inr"):
        out.append(f"turnover {money(f['min_avg_turnover_inr'])}/yr")
    if f.get("years_experience"):
        yrs = re.sub(r"\D", "", f["years_experience"]) or "?"
        out.append(f"{yrs} yr{'s' if yrs != '1' else ''} experience")
    if f.get("past_performance"):
        out.append(f"past performance {f['past_performance']}")
    docs = (f.get("documents_required") or "").lower()
    if "oem authori" in docs:
        out.append("OEM authorisation")
    if f.get("epbg_percent"):
        out.append(f"{f['epbg_percent']:g}% performance security")
    return out


def summarize(t) -> str:
    d = t.details or {}
    f = d.get("doc_fields") or {}
    bits: List[str] = []
    w = what(t.title, t.description)
    if w:
        bits.append(w + ".")
    buyer = t.organization or t.department
    org = ", ".join(x for x in [buyer, t.ministry if t.ministry and t.ministry != buyer else None] if x)
    place = clean_place(t.location) or t.state
    if org or place:
        bits.append("Buyer: " + (org or "not stated") + (f" — {place}" if place else "") + ".")
    money_bits = []
    if t.estimated_value:
        money_bits.append(f"Est. value {money(t.estimated_value)}")
    if t.emd:
        money_bits.append(f"EMD {money(t.emd)}")
    elif f.get("emd_required") == "No":
        money_bits.append("no EMD")
    if t.contract_duration:
        money_bits.append(f"contract {t.contract_duration}")
    if t.quantity and t.quantity > 1:
        money_bits.append(f"qty {t.quantity:g}")
    if money_bits:
        bits.append(" · ".join(money_bits) + ".")
    dl = deadline_text(t.deadline_at)
    if dl:
        bits.append(dl + ".")
    req = requirements(f)
    if req:
        bits.append("Needs: " + ", ".join(req) + ".")
    if t.best_category and (t.relevance_score or 0) > 0:
        bits.append(f"Matches {CATEGORY_NAMES.get(t.best_category, t.best_category)} ({t.relevance_score}/100).")
    if (d.get("corrigenda_count") or 0) > 0:
        bits.append(f"{d['corrigenda_count']} corrigendum(s) issued.")
    if t.status and t.status != "active":
        bits.append(f"Status: {t.status.replace('_', ' ')}.")
    return " ".join(bits)
