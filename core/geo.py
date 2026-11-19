"""Approximate Indian state from a 6-digit PIN code or from a department name.
PIN prefixes follow India Post postal circles; boundaries are approximate (documented as such in the UI)."""
from __future__ import annotations

from typing import Iterable, Optional

STATES = ["Andaman and Nicobar", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chandigarh",
          "Chhattisgarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Goa", "Gujarat", "Haryana",
          "Himachal Pradesh", "Jammu and Kashmir", "Jharkhand", "Karnataka", "Kerala", "Ladakh", "Lakshadweep",
          "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Puducherry",
          "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand",
          "West Bengal"]

_P3 = {"160": "Chandigarh", "194": "Ladakh", "403": "Goa", "605": "Puducherry", "682": "Lakshadweep",
       "737": "Sikkim", "744": "Andaman and Nicobar", "396": "Dadra and Nagar Haveli and Daman and Diu",
       "790": "Arunachal Pradesh", "791": "Arunachal Pradesh", "792": "Arunachal Pradesh",
       "793": "Meghalaya", "794": "Meghalaya", "795": "Manipur", "796": "Mizoram", "797": "Nagaland",
       "798": "Nagaland", "799": "Tripura"}
for p in list(range(246, 250)) + [262, 263]:
    _P3[str(p)] = "Uttarakhand"
for p in range(814, 836):
    _P3[str(p)] = "Jharkhand"

_P2 = {"11": "Delhi", "12": "Haryana", "13": "Haryana", "14": "Punjab", "15": "Punjab", "16": "Punjab",
       "17": "Himachal Pradesh", "18": "Jammu and Kashmir", "19": "Jammu and Kashmir", "30": "Rajasthan",
       "31": "Rajasthan", "32": "Rajasthan", "33": "Rajasthan", "34": "Rajasthan", "36": "Gujarat", "37": "Gujarat",
       "38": "Gujarat", "39": "Gujarat", "40": "Maharashtra", "41": "Maharashtra", "42": "Maharashtra",
       "43": "Maharashtra", "44": "Maharashtra", "45": "Madhya Pradesh", "46": "Madhya Pradesh",
       "47": "Madhya Pradesh", "48": "Madhya Pradesh", "49": "Chhattisgarh", "50": "Telangana",
       "51": "Andhra Pradesh", "52": "Andhra Pradesh", "53": "Andhra Pradesh", "56": "Karnataka",
       "57": "Karnataka", "58": "Karnataka", "59": "Karnataka", "60": "Tamil Nadu", "61": "Tamil Nadu",
       "62": "Tamil Nadu", "63": "Tamil Nadu", "64": "Tamil Nadu", "67": "Kerala", "68": "Kerala", "69": "Kerala",
       "70": "West Bengal", "71": "West Bengal", "72": "West Bengal", "73": "West Bengal", "74": "West Bengal",
       "75": "Odisha", "76": "Odisha", "77": "Odisha", "78": "Assam", "80": "Bihar", "81": "Bihar", "82": "Bihar",
       "83": "Bihar", "84": "Bihar", "85": "Bihar"}
for p in range(20, 29):
    _P2[str(p)] = "Uttar Pradesh"


def state_from_pin(pin: Optional[str]) -> Optional[str]:
    if not pin or len(pin) != 6 or not pin.isdigit():
        return None
    return _P3.get(pin[:3]) or _P2.get(pin[:2])


def state_from_text(*texts: Optional[str]) -> Optional[str]:
    blob = " ".join(t for t in texts if t).lower()
    for s in sorted(STATES, key=len, reverse=True):
        if s.lower() in blob:
            return s
    return None


def matches_geography(state: Optional[str], pins: Iterable[str], states_filter, pin_prefixes) -> bool:
    if not states_filter and not pin_prefixes:
        return True
    if states_filter and state and state in states_filter:
        return True
    for p in pins or []:
        if any(str(p).startswith(str(x)) for x in pin_prefixes or []):
            return True
        if states_filter and state_from_pin(str(p)) in states_filter:
            return True
    return False
