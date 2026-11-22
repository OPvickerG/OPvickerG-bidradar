"""Where is the work, and how far is it from home?

Evidence, strongest first (only what the portal or the bid document states; nothing is guessed):
  1. delivery / consignee PIN codes in the bid document
  2. consignee place names in the document (GeM masks the street address but usually leaves the district)
  3. the buying office named in the document
  4. the title / item description
  5. the buyer organisation / department / listing location
  6. the state alone (from any of the above)

Result stored in tender.details["geo"]:
  {"place", "state", "km", "basis", "near"}  where near is
  "yes"   - a named place within the home radius
  "maybe" - only the state is known and part of that state is within the radius, or no location is stated
  "no"    - the work is known to be outside the radius
A tender is never hidden for lack of location ("maybe")."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from core.geo import state_from_pin, state_from_text
from core.places import AMBIGUOUS, PIN3, dense_coverage, find_places, fuzzy_place, haversine_km, state_min_km

STATE_MARGIN_KM = 50


def _margin(radius_km: float) -> float:
    """Extra distance allowed for state-only bids: 50 km, or half the radius when the radius is small."""
    return min(STATE_MARGIN_KM, radius_km / 2)
_CONSIGNEE_RX = re.compile(r"(Consignees?/Reporting Officer|Consignee Reporting/Officer|/Delivery Days)", re.I)
_PIN_RX = re.compile(r"(?<![\d.,])([1-8]\d{5})(?![\d.])(?!,\d)")     # '825301,ZULU PARK' counts; '100000.00' does not
# GeM masks consignee street addresses but leaves the district: '1 ***********South Goa 1 N/A', 'Dist - Purulia'
_DISTRICT_RX = re.compile(r"\*{4,}\s*([A-Za-z][A-Za-z .()&-]{1,40}?)\s+\d|\bDist(?:rict)?\s*[-:.]\s*([A-Za-z][A-Za-z ]{2,30}?)(?=[,./]|\s+[A-Z][a-z]+\s*/|\s+Project|$)")


def consignee_text(doc_text: Optional[str]) -> str:
    """The consignee / delivery sections of a GeM bid document (where the work or delivery happens)."""
    if not doc_text:
        return ""
    return " ".join(doc_text[m.start(): m.start() + 700] for m in _CONSIGNEE_RX.finditer(doc_text))


def _home(home: Optional[Dict]) -> Optional[Tuple[float, float]]:
    if home and home.get("lat") is not None and home.get("lon") is not None:
        return float(home["lat"]), float(home["lon"])
    return None


def _classify(km: Optional[float], home: Optional[Dict]) -> Optional[str]:
    if km is None or not _home(home):
        return None
    return "yes" if km <= float(home.get("radius_km") or 250) else "no"


def locate(t, home: Optional[Dict] = None) -> Dict:
    d = t.details or {}
    f = d.get("doc_fields") or {}
    hp = _home(home)
    cons = consignee_text(d.get("doc_text"))
    pins: List[str] = list(dict.fromkeys((f.get("consignee_pins") or []) + _PIN_RX.findall(cons)))
    pins = [p for p in pins if state_from_pin(p)]
    hint = t.state or next((state_from_pin(p) for p in pins), None) or state_from_text(cons, f.get("office"))

    candidates: List[Tuple[str, float, float, str, str]] = []      # name, lat, lon, state, basis
    for p in pins:
        if p[:3] in PIN3:
            name, la, lo, st = PIN3[p[:3]]
            candidates.append((name, la, lo, st, f"delivery PIN {p}"))
    if not candidates:
        candidates = [(n, la, lo, st, "delivery place in bid document") for n, la, lo, st in find_places(cons, hint)]
    districts = [x.strip() for m in _DISTRICT_RX.findall(cons) for x in m if x and x.strip()]
    if not candidates:
        for dname in districts:
            fp = fuzzy_place(dname)
            if fp:
                candidates.append((fp[0], fp[1], fp[2], fp[3], "delivery district in bid document"))
    if not candidates and districts and dense_coverage(home) \
            and not any(dn.lower() in AMBIGUOUS for dn in districts) \
            and not state_from_text(cons) in (None,) + tuple(_near_states(home)):
        # a named delivery district that is not any place in (or near) the home area: outside the radius
        return {"place": districts[0].title(), "state": state_from_text(cons), "km": None,
                "basis": "delivery district in bid document", "near": "no"}
    if not candidates:
        for text, basis in ((f.get("office"), "buying office"), (f"{t.title or ''} {t.description or ''}", "title"),
                            (" ".join(x for x in (t.location, t.organization, t.department) if x), "buyer name")):
            found = find_places(text, hint)
            if found:
                candidates = [(n, la, lo, st, basis) for n, la, lo, st in found]
                break

    if candidates:
        # several delivery points: the one nearest home decides (that is the one you would serve)
        if hp:
            candidates.sort(key=lambda c: haversine_km(hp, (c[1], c[2])))
        name, la, lo, st, basis = candidates[0]
        km = round(haversine_km(hp, (la, lo))) if hp else None
        g = {"place": name, "state": st, "km": km, "basis": basis, "near": _classify(km, home)}
        if len(candidates) > 1:
            g["more_places"] = len(candidates) - 1
        return g

    state = next((state_from_pin(p) for p in pins), None) or state_from_text(cons) or t.state or \
        state_from_text(f.get("office"), t.organization, t.department, t.location)
    if state:
        near = None
        if hp:
            # a state's border lies nearer than its nearest listed town: keep a 50 km margin so it is never hidden wrongly
            r = float(home.get("radius_km") or 250)
            near = "maybe" if state_min_km(state, hp) <= r + _margin(r) else "no"
        return {"place": None, "state": state, "km": None, "basis": "state only", "near": near}
    return {"place": None, "state": None, "km": None, "basis": "not stated", "near": "maybe" if hp else None}


def _near_states(home: Optional[Dict]) -> List[str]:
    hp = _home(home)
    if not hp:
        return []
    from core.geo import STATES
    r = float(home.get("radius_km") or 250)
    return [s for s in STATES if state_min_km(s, hp) <= r + _margin(r)]


def geo_label(g: Optional[Dict], home_name: Optional[str] = None) -> str:
    """Short text for lists and emails, e.g. 'Panipat, Haryana · 34 km from Karnal'."""
    if not g:
        return ""
    if g.get("place"):
        s = g["place"] + (f", {g['state']}" if g.get("state") and g["state"] not in g["place"] else "")
        if g.get("more_places"):
            s += f" (+{g['more_places']} more)"
        if g.get("km") is not None:
            s += f" · {g['km']} km" + (f" from {home_name}" if home_name else "")
        return s
    if g.get("state"):
        return f"{g['state']} (exact place not stated)"
    return "location not stated"
