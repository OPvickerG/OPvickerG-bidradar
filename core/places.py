"""Small built-in gazetteer for "how far is this tender from me?".

Approximate coordinates (to within a few km) of district towns and cantonments, dense across North India
(Haryana, Punjab, Chandigarh, Himachal, Delhi, Uttarakhand, western UP, J&K, north Rajasthan) plus the main cities of
the rest of India, and approximate centres of 3-digit PIN areas in the north. Distances are straight-line km.
This is generic public geography; the user's own home place lives only in the database settings."""
from __future__ import annotations

import math
import re
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

H, P, C, HP, D, UK, UP, JK, RJ = ("Haryana", "Punjab", "Chandigarh", "Himachal Pradesh", "Delhi", "Uttarakhand",
                                  "Uttar Pradesh", "Jammu and Kashmir", "Rajasthan")

# name -> (lat, lon, state). Lower-case keys; several spellings point at the same place.
PLACES: Dict[str, Tuple[float, float, str]] = {
    # Haryana
    "ambala": (30.37, 76.80, H), "ambala city": (30.36, 76.80, H), "ambala cantt": (30.34, 76.84, H),
    "ambala cantonment": (30.34, 76.84, H), "naraingarh": (30.48, 77.13, H), "barara": (30.21, 77.03, H),
    "mullana": (30.27, 77.05, H), "shahzadpur": (30.40, 77.10, H), "panchkula": (30.69, 76.86, H),
    "chandimandir": (30.73, 76.89, H), "kalka": (30.84, 76.94, H), "pinjore": (30.80, 76.92, H),
    "raipur rani": (30.59, 77.03, H), "yamunanagar": (30.13, 77.29, H), "yamuna nagar": (30.13, 77.29, H),
    "jagadhri": (30.17, 77.30, H), "radaur": (30.03, 77.15, H), "kurukshetra": (29.97, 76.88, H),
    "thanesar": (29.97, 76.83, H), "pehowa": (29.98, 76.58, H), "shahabad markanda": (30.17, 76.87, H),
    "ladwa": (29.99, 77.05, H), "kaithal": (29.80, 76.40, H), "guhla": (30.03, 76.33, H),
    "karnal": (29.69, 76.99, H), "gharaunda": (29.54, 76.97, H), "nilokheri": (29.83, 76.92, H),
    "taraori": (29.80, 76.92, H), "panipat": (29.39, 76.97, H), "samalkha": (29.24, 77.01, H),
    "sonipat": (28.99, 77.02, H), "sonepat": (28.99, 77.02, H), "gohana": (29.14, 76.70, H),
    "ganaur": (29.13, 77.02, H), "murthal": (29.03, 77.06, H), "kundli": (28.88, 77.12, H),
    "rohtak": (28.90, 76.61, H), "jhajjar": (28.61, 76.66, H), "bahadurgarh": (28.69, 76.93, H),
    "jind": (29.32, 76.31, H), "narwana": (29.60, 76.12, H), "safidon": (29.41, 76.67, H),
    "hisar": (29.15, 75.72, H), "hissar": (29.15, 75.72, H), "hansi": (29.10, 75.97, H),
    "fatehabad": (29.52, 75.45, H), "tohana": (29.71, 75.90, H), "sirsa": (29.53, 75.03, H),
    "dabwali": (29.95, 74.73, H), "bhiwani": (28.79, 76.13, H), "charkhi dadri": (28.59, 76.27, H),
    "loharu": (28.43, 75.81, H), "rewari": (28.20, 76.62, H), "dharuhera": (28.21, 76.80, H),
    "bawal": (28.08, 76.58, H), "narnaul": (28.04, 76.11, H), "mahendragarh": (28.27, 76.15, H),
    "gurugram": (28.46, 77.03, H), "gurgaon": (28.46, 77.03, H), "manesar": (28.36, 76.94, H),
    "sohna": (28.25, 77.07, H), "faridabad": (28.41, 77.32, H), "ballabgarh": (28.34, 77.32, H),
    "palwal": (28.14, 77.33, H), "hodal": (27.89, 77.37, H), "nuh": (28.10, 76.99, H), "mewat": (28.10, 76.99, H),
    # Chandigarh
    "chandigarh": (30.73, 76.78, C),
    # Punjab
    "mohali": (30.70, 76.72, P), "sas nagar": (30.70, 76.72, P), "sahibzada ajit singh nagar": (30.70, 76.72, P),
    "kharar": (30.75, 76.65, P), "zirakpur": (30.64, 76.82, P), "dera bassi": (30.59, 76.84, P),
    "derabassi": (30.59, 76.84, P), "lalru": (30.48, 76.80, P), "banur": (30.55, 76.72, P),
    "rajpura": (30.48, 76.59, P), "patiala": (30.34, 76.39, P), "nabha": (30.37, 76.15, P),
    "samana": (30.15, 76.19, P), "fatehgarh sahib": (30.65, 76.39, P), "sirhind": (30.64, 76.38, P),
    "mandi gobindgarh": (30.67, 76.30, P), "khanna": (30.70, 76.22, P), "rupnagar": (30.97, 76.53, P),
    "ropar": (30.97, 76.53, P), "nangal": (31.39, 76.38, P), "anandpur sahib": (31.24, 76.50, P),
    "morinda": (30.79, 76.50, P), "ludhiana": (30.90, 75.85, P), "jagraon": (30.79, 75.47, P),
    "doraha": (30.80, 76.02, P), "sangrur": (30.25, 75.84, P), "malerkotla": (30.53, 75.88, P),
    "sunam": (30.13, 75.80, P), "barnala": (30.38, 75.55, P), "bathinda": (30.21, 74.95, P),
    "bhatinda": (30.21, 74.95, P), "mansa": (29.99, 75.40, P), "moga": (30.82, 75.17, P),
    "faridkot": (30.67, 74.76, P), "kotkapura": (30.58, 74.82, P), "muktsar": (30.47, 74.52, P),
    "sri muktsar sahib": (30.47, 74.52, P), "malout": (30.19, 74.50, P), "abohar": (30.14, 74.20, P),
    "fazilka": (30.40, 74.03, P), "ferozepur": (30.93, 74.61, P), "firozpur": (30.93, 74.61, P),
    "ferozepur cantt": (30.95, 74.62, P), "jalandhar": (31.33, 75.58, P), "jalandhar cantt": (31.29, 75.63, P),
    "kapurthala": (31.38, 75.38, P), "phagwara": (31.22, 75.77, P), "nawanshahr": (31.12, 76.12, P),
    "shaheed bhagat singh nagar": (31.12, 76.12, P), "hoshiarpur": (31.53, 75.91, P), "dasuya": (31.82, 75.65, P),
    "mukerian": (31.95, 75.62, P), "amritsar": (31.63, 74.87, P), "tarn taran": (31.45, 74.93, P),
    "gurdaspur": (32.04, 75.40, P), "batala": (31.82, 75.20, P), "pathankot": (32.27, 75.65, P),
    "mamun": (32.29, 75.66, P), "beas": (31.52, 75.29, P),
    # Himachal Pradesh
    "shimla": (31.10, 77.17, HP), "jutogh": (31.10, 77.13, HP), "solan": (30.91, 77.10, HP),
    "kasauli": (30.90, 76.97, HP), "dagshai": (30.89, 77.05, HP), "subathu": (31.00, 76.99, HP),
    "sabathu": (31.00, 76.99, HP), "parwanoo": (30.84, 76.96, HP), "baddi": (30.96, 76.79, HP),
    "nalagarh": (31.05, 76.72, HP), "nahan": (30.56, 77.30, HP), "paonta sahib": (30.44, 77.62, HP),
    "sirmaur": (30.56, 77.30, HP), "sundernagar": (31.53, 76.89, HP), "kullu": (31.96, 77.11, HP),
    "manali": (32.24, 77.19, HP), "una": (31.47, 76.27, HP), "kangra": (32.10, 76.27, HP),
    "dharamshala": (32.22, 76.32, HP), "palampur": (32.11, 76.54, HP), "yol": (32.18, 76.40, HP),
    "chamba": (32.56, 76.13, HP), "dalhousie": (32.54, 75.97, HP), "reckong peo": (31.54, 78.27, HP),
    "kinnaur": (31.54, 78.27, HP), "lahaul and spiti": (32.57, 77.03, HP), "keylong": (32.57, 77.03, HP),
    "rampur bushahr": (31.45, 77.63, HP), "nurpur": (32.30, 75.89, HP), "ghumarwin": (31.44, 76.71, HP),
    # Delhi
    "delhi": (28.61, 77.21, D), "new delhi": (28.61, 77.21, D), "delhi cantt": (28.59, 77.13, D),
    "delhi cantonment": (28.59, 77.13, D), "dwarka": (28.59, 77.05, D), "rohini": (28.74, 77.07, D),
    "narela": (28.85, 77.09, D), "shahdara": (28.67, 77.29, D), "okhla": (28.53, 77.27, D),
    # Uttarakhand
    "dehradun": (30.32, 78.03, UK), "dehra dun": (30.32, 78.03, UK), "clement town": (30.27, 78.01, UK),
    "mussoorie": (30.46, 78.07, UK), "haridwar": (29.95, 78.16, UK), "roorkee": (29.87, 77.89, UK),
    "rishikesh": (30.09, 78.27, UK), "raiwala": (30.02, 78.22, UK), "lansdowne": (29.84, 78.68, UK),
    "kotdwar": (29.75, 78.52, UK), "pauri": (30.15, 78.78, UK), "tehri": (30.38, 78.43, UK),
    "rudrapur": (28.98, 79.40, UK), "haldwani": (29.22, 79.51, UK), "nainital": (29.38, 79.46, UK),
    "ranikhet": (29.64, 79.43, UK), "almora": (29.60, 79.66, UK), "kashipur": (29.21, 78.96, UK),
    "pithoragarh": (29.58, 80.21, UK), "bageshwar": (29.84, 79.77, UK), "chamoli": (30.40, 79.32, UK),
    "gopeshwar": (30.40, 79.32, UK), "champawat": (29.34, 80.09, UK), "rudraprayag": (30.28, 78.98, UK),
    "uttarkashi": (30.73, 78.45, UK), "udham singh nagar": (28.98, 79.40, UK), "pauri garhwal": (30.15, 78.78, UK),
    "tehri garhwal": (30.38, 78.43, UK), "joshimath": (30.56, 79.56, UK),
    # Uttar Pradesh (west first)
    "saharanpur": (29.96, 77.55, UP), "muzaffarnagar": (29.47, 77.70, UP), "shamli": (29.45, 77.31, UP),
    "meerut": (28.98, 77.71, UP), "meerut cantt": (29.00, 77.69, UP), "baghpat": (28.94, 77.22, UP),
    "ghaziabad": (28.67, 77.45, UP), "noida": (28.54, 77.39, UP), "greater noida": (28.47, 77.50, UP),
    "gautam buddha nagar": (28.54, 77.39, UP), "hapur": (28.73, 77.78, UP), "bulandshahr": (28.41, 77.85, UP),
    "bijnor": (29.37, 78.14, UP), "moradabad": (28.84, 78.77, UP), "aligarh": (27.88, 78.08, UP),
    "mathura": (27.49, 77.67, UP), "agra": (27.18, 78.01, UP), "bareilly": (28.37, 79.43, UP),
    "lucknow": (26.85, 80.95, UP), "kanpur": (26.45, 80.33, UP), "jhansi": (25.45, 78.57, UP),
    "prayagraj": (25.44, 81.85, UP), "allahabad": (25.44, 81.85, UP), "varanasi": (25.32, 82.97, UP),
    "gorakhpur": (26.76, 83.37, UP), "amroha": (28.90, 78.47, UP), "jyotiba phule nagar": (28.90, 78.47, UP),
    "rampur": (28.80, 79.03, UP), "sambhal": (28.58, 78.57, UP), "deoband": (29.69, 77.68, UP),
    # Jammu & Kashmir / Ladakh
    "jammu": (32.73, 74.86, JK), "samba": (32.56, 75.12, JK), "kathua": (32.37, 75.52, JK),
    "udhampur": (32.92, 75.14, JK), "akhnoor": (32.89, 74.74, JK), "rajouri": (33.38, 74.31, JK),
    "srinagar": (34.08, 74.80, JK), "leh": (34.16, 77.58, "Ladakh"), "reasi": (33.08, 74.83, JK),
    "poonch": (33.77, 74.09, JK), "doda": (33.15, 75.55, JK), "kishtwar": (33.31, 75.77, JK),
    "ramban": (33.24, 75.24, JK), "pathankot cantt": (32.27, 75.65, P),
    # Rajasthan
    "sri ganganagar": (29.90, 73.88, RJ), "ganganagar": (29.90, 73.88, RJ), "hanumangarh": (29.58, 74.33, RJ),
    "suratgarh": (29.32, 73.90, RJ), "alwar": (27.55, 76.60, RJ), "bhiwadi": (28.21, 76.86, RJ),
    "jhunjhunu": (28.13, 75.40, RJ), "churu": (28.30, 74.95, RJ), "sikar": (27.61, 75.14, RJ),
    "bikaner": (28.02, 73.31, RJ), "jaipur": (26.91, 75.79, RJ), "jodhpur": (26.24, 73.02, RJ),
    "khairthal": (27.80, 76.64, RJ), "kotputli": (27.70, 76.20, RJ),
    "ajmer": (26.45, 74.64, RJ), "udaipur": (24.59, 73.71, RJ), "kota": (25.18, 75.83, RJ),
    # rest of India: main cities (so far-away tenders get a distance too)
    "mumbai": (19.08, 72.88, "Maharashtra"), "pune": (18.52, 73.86, "Maharashtra"),
    "nagpur": (21.15, 79.09, "Maharashtra"), "nashik": (20.00, 73.79, "Maharashtra"),
    "kolkata": (22.57, 88.36, "West Bengal"), "siliguri": (26.73, 88.40, "West Bengal"),
    "chennai": (13.08, 80.27, "Tamil Nadu"), "thiruvallur": (13.14, 79.91, "Tamil Nadu"),
    "coimbatore": (11.02, 76.96, "Tamil Nadu"), "madurai": (9.93, 78.12, "Tamil Nadu"),
    "bengaluru": (12.97, 77.59, "Karnataka"), "bangalore": (12.97, 77.59, "Karnataka"),
    "mysuru": (12.30, 76.64, "Karnataka"), "belagavi": (15.85, 74.50, "Karnataka"),
    "hyderabad": (17.39, 78.49, "Telangana"), "secunderabad": (17.44, 78.50, "Telangana"),
    "visakhapatnam": (17.69, 83.22, "Andhra Pradesh"), "vijayawada": (16.51, 80.65, "Andhra Pradesh"),
    "ahmedabad": (23.02, 72.57, "Gujarat"), "vadodara": (22.31, 73.18, "Gujarat"), "surat": (21.17, 72.83, "Gujarat"),
    "gandhinagar": (23.22, 72.65, "Gujarat"), "jamnagar": (22.47, 70.06, "Gujarat"),
    "bhopal": (23.26, 77.41, "Madhya Pradesh"), "indore": (22.72, 75.86, "Madhya Pradesh"),
    "jabalpur": (23.18, 79.99, "Madhya Pradesh"), "gwalior": (26.22, 78.18, "Madhya Pradesh"),
    "mhow": (22.55, 75.76, "Madhya Pradesh"), "patna": (25.59, 85.14, "Bihar"), "gaya": (24.79, 85.00, "Bihar"),
    "ranchi": (23.34, 85.31, "Jharkhand"), "jamshedpur": (22.80, 86.20, "Jharkhand"),
    "dhanbad": (23.80, 86.43, "Jharkhand"), "bhubaneswar": (20.30, 85.82, "Odisha"),
    "cuttack": (20.46, 85.88, "Odisha"), "guwahati": (26.14, 91.74, "Assam"), "tezpur": (26.63, 92.80, "Assam"),
    "kochi": (9.93, 76.27, "Kerala"), "thiruvananthapuram": (8.52, 76.94, "Kerala"),
    "panaji": (15.49, 73.83, "Goa"), "south goa": (15.20, 74.00, "Goa"), "north goa": (15.55, 73.85, "Goa"),
    "vasco da gama": (15.40, 73.81, "Goa"), "port blair": (11.62, 92.73, "Andaman and Nicobar"),
    "durg": (21.19, 81.28, "Chhattisgarh"), "bhilai": (21.21, 81.38, "Chhattisgarh"),
}

# Names shared by places in different states: resolved only with a state hint from the same tender.
AMBIGUOUS: Dict[str, List[Tuple[float, float, str]]] = {
    "bilaspur": [(31.33, 76.76, HP), (22.08, 82.15, "Chhattisgarh")],
    "hamirpur": [(31.68, 76.52, HP), (25.95, 80.15, UP)],
    "mandi": [(31.71, 76.93, HP)],          # also a common word ("Krishi Upaj Mandi"): needs the HP hint
    "raipur": [(21.25, 81.63, "Chhattisgarh")],
    "aurangabad": [(19.88, 75.34, "Maharashtra"), (24.75, 84.37, "Bihar")],
    "pratapgarh": [(25.90, 81.95, UP), (24.03, 74.78, RJ)],
}

# Approximate centres of 3-digit PIN areas across the north (others fall back to their state).
PIN3: Dict[str, Tuple[str, float, float, str]] = {
    "110": ("Delhi", 28.61, 77.21, D), "121": ("Faridabad", 28.41, 77.32, H), "122": ("Gurugram", 28.46, 77.03, H),
    "123": ("Rewari", 28.20, 76.62, H), "124": ("Rohtak", 28.90, 76.61, H), "125": ("Hisar", 29.15, 75.72, H),
    "126": ("Jind", 29.32, 76.31, H), "127": ("Bhiwani", 28.79, 76.13, H), "131": ("Sonipat", 28.99, 77.02, H),
    "132": ("Karnal / Panipat", 29.55, 76.98, H), "133": ("Ambala", 30.36, 76.83, H),
    "134": ("Panchkula", 30.69, 76.86, H), "135": ("Yamunanagar", 30.13, 77.29, H),
    "136": ("Kurukshetra / Kaithal", 29.92, 76.70, H), "140": ("Mohali / Rupnagar", 30.85, 76.60, P),
    "141": ("Ludhiana", 30.90, 75.85, P), "142": ("Moga", 30.82, 75.17, P), "143": ("Amritsar", 31.63, 74.87, P),
    "144": ("Jalandhar", 31.33, 75.58, P), "145": ("Pathankot", 32.27, 75.65, P),
    "146": ("Hoshiarpur", 31.53, 75.91, P), "147": ("Patiala", 30.34, 76.39, P), "148": ("Sangrur", 30.25, 75.84, P),
    "151": ("Bathinda", 30.21, 74.95, P), "152": ("Ferozepur / Faridkot", 30.70, 74.40, P),
    "160": ("Chandigarh", 30.73, 76.78, C), "171": ("Shimla", 31.10, 77.17, HP), "172": ("Shimla / Kinnaur", 31.40, 77.60, HP),
    "173": ("Solan / Sirmaur", 30.90, 77.10, HP), "174": ("Bilaspur / Una (HP)", 31.40, 76.60, HP),
    "175": ("Mandi / Kullu", 31.80, 77.00, HP), "176": ("Kangra / Chamba", 32.15, 76.30, HP),
    "177": ("Hamirpur / Una (HP)", 31.60, 76.40, HP), "180": ("Jammu", 32.73, 74.86, JK), "181": ("Jammu", 32.70, 74.90, JK),
    "182": ("Udhampur", 32.92, 75.14, JK), "184": ("Kathua", 32.37, 75.52, JK), "185": ("Rajouri / Poonch", 33.40, 74.30, JK),
    "190": ("Srinagar", 34.08, 74.80, JK), "201": ("Ghaziabad / Noida", 28.62, 77.42, UP), "202": ("Aligarh", 27.88, 78.08, UP),
    "203": ("Bulandshahr", 28.41, 77.85, UP), "244": ("Moradabad", 28.84, 78.77, UP), "245": ("Hapur", 28.73, 77.78, UP),
    "246": ("Bijnor / Pauri", 29.60, 78.40, UK), "247": ("Saharanpur / Roorkee", 29.90, 77.70, UP),
    "248": ("Dehradun", 30.32, 78.03, UK), "249": ("Haridwar / Rishikesh", 30.00, 78.20, UK),
    "250": ("Meerut", 28.98, 77.71, UP), "251": ("Muzaffarnagar", 29.47, 77.70, UP),
    "262": ("Pithoragarh", 29.50, 80.10, UK), "263": ("Nainital / Almora", 29.40, 79.50, UK),
    "301": ("Alwar", 27.55, 76.60, RJ), "302": ("Jaipur", 26.91, 75.79, RJ), "331": ("Churu", 28.20, 75.20, RJ),
    "333": ("Jhunjhunu", 28.13, 75.40, RJ), "334": ("Bikaner", 28.02, 73.31, RJ), "335": ("Sri Ganganagar", 29.90, 73.88, RJ),
}


def haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def state_min_km(state: str, home: Tuple[float, float]) -> float:
    """Distance from home to the nearest known place of that state (an optimistic 'could be this close')."""
    pts = [(la, lo) for la, lo, st in PLACES.values() if st == state]
    pts += [(la, lo) for _, la, lo, st in PIN3.values() if st == state]
    return min((haversine_km(home, p) for p in pts), default=1500.0)


@lru_cache(maxsize=1)
def _rx():
    names = sorted(list(PLACES) + list(AMBIGUOUS), key=len, reverse=True)
    return re.compile(r"(?<![a-z])(" + "|".join(re.escape(n) for n in names) + r")(?![a-z])")


def find_places(text: Optional[str], state_hint: Optional[str] = None) -> List[Tuple[str, float, float, str]]:
    """All known places named in the text, in order of appearance: (Name, lat, lon, state)."""
    if not text:
        return []
    low = re.sub(r"[\s_]+", " ", text.lower())
    hints = {state_hint} if state_hint else set()
    hints |= {st for st in {v[2] for v in PLACES.values()} if st.lower() in low}
    out, seen = [], set()
    for m in _rx().finditer(low):
        n = m.group(1)
        if n in seen:
            continue
        if n in PLACES:
            la, lo, st = PLACES[n]
        else:
            opts = [o for o in AMBIGUOUS[n] if o[2] in hints]
            if len(opts) != 1:
                continue
            la, lo, st = opts[0]
        seen.add(n)
        out.append((n.title().replace("Cantt", "Cantt").replace("Sas ", "SAS "), la, lo, st))
    return out


# Every district (and most towns) within roughly 300 km of this box is in PLACES, so inside it an unrecognised
# delivery district can safely be called "outside the area".
DENSE_ZONE = (28.0, 33.0, 74.0, 79.5)       # lat_min, lat_max, lon_min, lon_max


def dense_coverage(home: Optional[Dict]) -> bool:
    if not home or home.get("lat") is None:
        return False
    la, lo = float(home["lat"]), float(home["lon"])
    return (DENSE_ZONE[0] <= la <= DENSE_ZONE[1] and DENSE_ZONE[2] <= lo <= DENSE_ZONE[3]
            and float(home.get("radius_km") or 250) <= 300)


def fuzzy_place(name: str) -> Optional[Tuple[str, float, float, str]]:
    """Tolerate small misspellings in district names ('Kurukshtra'); ambiguous names are not resolved."""
    from rapidfuzz import fuzz, process
    n = re.sub(r"\s+", " ", name.lower()).strip()
    if n in AMBIGUOUS:
        return None
    hit = process.extractOne(n, list(PLACES), scorer=fuzz.ratio, score_cutoff=88)
    if not hit:
        return None
    la, lo, st = PLACES[hit[0]]
    return hit[0].title(), la, lo, st


def resolve_home(name: str) -> Optional[Tuple[str, float, float, str]]:
    found = find_places(name)
    return found[0] if found else None
