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
    # small towns close to the north-Haryana / Chandigarh area (so nothing nearby is ever called "far")
    "saha": (30.30, 76.97, H), "sadhaura": (30.38, 77.22, H), "chhachhrauli": (30.25, 77.36, H),
    "mustafabad": (30.20, 77.15, H), "morni": (30.70, 77.07, H), "indri": (29.88, 77.06, H), "pipli": (29.98, 76.88, H),
    "jagadhari": (30.17, 77.30, H), "jagadhri workshop": (30.17, 77.30, H), "kala amb": (30.51, 77.21, HP),
    "barotiwala": (30.89, 76.84, HP), "kurali": (30.83, 76.58, P), "dappar": (30.56, 76.86, P),
    "behat": (30.17, 77.62, UP), "tibri": (32.08, 75.37, P), "sarsawa": (30.02, 77.40, UP),
    # districts and stations that GeM bid documents name (often in their own spelling)
    "anantnag": (33.73, 75.15, JK), "ananthnag": (33.73, 75.15, JK), "rajauri": (33.38, 74.31, JK),
    "kupwara": (34.53, 74.26, JK), "baramulla": (34.20, 74.34, JK), "uri": (34.08, 74.03, JK),
    "akhnoor": (32.87, 74.74, JK), "nagrota": (32.80, 74.93, JK), "samba": (32.56, 75.12, JK), "gurez": (34.63, 74.84, JK),
    "kargil": (34.56, 76.13, "Ladakh"), "drass": (34.43, 75.75, "Ladakh"), "partapur": (34.70, 77.47, "Ladakh"),
    "jalpaiguri": (26.52, 88.72, "West Bengal"), "binnaguri": (26.77, 89.04, "West Bengal"),
    "sukna": (26.79, 88.36, "West Bengal"), "bengdubi": (26.80, 88.40, "West Bengal"),
    "panagarh": (23.45, 87.43, "West Bengal"), "barrackpore": (22.76, 88.37, "West Bengal"),
    "purulia": (23.33, 86.36, "West Bengal"), "tawang": (27.59, 91.86, "Arunachal Pradesh"),
    "bomdila": (27.26, 92.42, "Arunachal Pradesh"), "likabali": (27.66, 94.70, "Arunachal Pradesh"),
    "aalo": (28.17, 94.80, "Arunachal Pradesh"), "dimapur": (25.91, 93.73, "Nagaland"),
    "zakhama": (25.62, 94.10, "Nagaland"), "leimakhong": (24.93, 93.85, "Manipur"),
    "dibrugarh": (27.47, 94.91, "Assam"), "missamari": (26.82, 92.60, "Assam"), "rangiya": (26.45, 91.62, "Assam"),
    "narangi": (26.18, 91.82, "Assam"), "masimpur": (24.81, 92.80, "Assam"), "barmer": (25.75, 71.39, RJ),
    "jaisalmer": (26.92, 70.91, RJ), "pokhran": (26.92, 71.92, RJ), "amreli": (21.60, 71.22, "Gujarat"),
    "bhuj": (23.25, 69.67, "Gujarat"), "babina": (25.25, 78.47, UP), "mhow": (22.55, 75.76, "Madhya Pradesh"),
    "deolali": (19.95, 73.83, "Maharashtra"), "wellington": (11.37, 76.79, "Tamil Nadu"),
    "hazaribagh": (23.99, 85.36, "Jharkhand"), "kakinada": (16.96, 82.24, "Andhra Pradesh"),
    "chengalpattu": (12.69, 79.98, "Tamil Nadu"), "kalpakkam": (12.52, 80.16, "Tamil Nadu"),
    "mirzapur": (25.15, 82.57, UP),
}

# Names shared by places in different states: resolved only with a state hint from the same tender.
AMBIGUOUS: Dict[str, List[Tuple[float, float, str]]] = {
    "barwala": [(30.60, 76.97, H), (29.37, 75.91, H)],
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

# The rest of India (approximate centres of each 3-digit PIN area). With this table a PIN anywhere in a bid
# document gives a place and a distance, not just a state.
_PIN3_REST = """
183 Jammu|32.70|74.90|JK 186 Kathua|32.40|75.50|JK 191 Srinagar / Budgam|34.00|74.75|JK 192 Anantnag / Pulwama|33.73|75.15|JK
193 Baramulla / Kupwara|34.20|74.34|JK 194 Leh / Kargil|34.16|77.58|LA
204 Hathras|27.60|78.05|UP 205 Mainpuri|27.23|79.02|UP 206 Etawah / Auraiya|26.78|79.02|UP 207 Etah / Kasganj|27.56|78.66|UP
208 Kanpur|26.45|80.33|UP 209 Kanpur Dehat / Unnao / Kannauj|26.70|80.10|UP 210 Banda / Hamirpur / Mahoba|25.48|80.33|UP
211 Prayagraj|25.44|81.85|UP 212 Fatehpur / Kaushambi|25.80|81.20|UP 221 Varanasi|25.32|82.97|UP 222 Jaunpur|25.75|82.69|UP
223 Jaunpur / Bhadohi|25.60|82.50|UP 224 Ayodhya / Ambedkar Nagar|26.60|82.30|UP 225 Barabanki|26.93|81.20|UP
226 Lucknow|26.85|80.95|UP 227 Lucknow rural|26.90|80.80|UP 228 Sultanpur / Amethi|26.26|82.07|UP 229 Raebareli|26.23|81.23|UP
230 Pratapgarh (UP)|25.90|81.95|UP 231 Mirzapur / Sonbhadra|24.90|82.80|UP 232 Chandauli|25.27|83.27|UP 233 Ghazipur|25.58|83.58|UP
241 Hardoi|27.40|80.13|UP 242 Shahjahanpur|27.88|79.91|UP 243 Bareilly / Budaun|28.37|79.43|UP 252 Rampur|28.80|79.03|UP
261 Sitapur / Lakhimpur|27.57|80.68|UP 271 Gonda / Bahraich|27.13|81.96|UP 272 Basti / Siddharthnagar|26.80|82.73|UP
273 Gorakhpur|26.76|83.37|UP 274 Deoria / Kushinagar|26.50|83.78|UP 275 Mau|25.94|83.56|UP 276 Azamgarh|26.07|83.18|UP
277 Ballia|25.76|84.15|UP 281 Mathura|27.49|77.67|UP 282 Agra|27.18|78.01|UP 283 Firozabad|27.15|78.40|UP
284 Jhansi / Lalitpur|25.45|78.57|UP 285 Jalaun / Orai|25.99|79.45|UP
303 Jaipur rural|27.00|75.90|RJ 304 Tonk|26.17|75.79|RJ 305 Ajmer|26.45|74.64|RJ 306 Pali|25.77|73.32|RJ 307 Sirohi|24.88|72.86|RJ
311 Bhilwara|25.35|74.63|RJ 312 Chittorgarh|24.88|74.62|RJ 313 Udaipur|24.58|73.71|RJ 314 Dungarpur|23.84|73.71|RJ
321 Bharatpur|27.22|77.49|RJ 322 Sawai Madhopur / Karauli|26.00|76.35|RJ 323 Bundi|25.44|75.64|RJ 324 Kota|25.18|75.83|RJ
325 Baran|25.10|76.51|RJ 326 Jhalawar|24.60|76.16|RJ 327 Banswara|23.55|74.44|RJ 328 Dholpur|26.70|77.89|RJ 332 Sikar|27.61|75.14|RJ
341 Nagaur|27.20|73.73|RJ 342 Jodhpur|26.24|73.02|RJ 343 Jalore|25.35|72.62|RJ 344 Barmer|25.75|71.39|RJ 345 Jaisalmer|26.92|70.90|RJ
360 Rajkot|22.30|70.80|GJ 361 Jamnagar|22.47|70.06|GJ 362 Junagadh|21.52|70.46|GJ 363 Surendranagar / Morbi|22.73|71.64|GJ
364 Bhavnagar|21.76|72.15|GJ 365 Amreli|21.60|71.22|GJ 370 Kutch|23.25|69.67|GJ 380 Ahmedabad|23.02|72.57|GJ
382 Gandhinagar|23.22|72.65|GJ 383 Sabarkantha|23.60|73.00|GJ 384 Mehsana / Patan|23.60|72.38|GJ 385 Banaskantha|24.17|72.43|GJ
387 Kheda|22.75|72.68|GJ 388 Anand|22.56|72.95|GJ 389 Godhra / Dahod|22.77|73.61|GJ 390 Vadodara|22.31|73.18|GJ
391 Vadodara rural|22.30|73.30|GJ 392 Bharuch|21.70|72.98|GJ 393 Narmada|21.87|73.50|GJ 394 Surat|21.17|72.83|GJ
395 Surat|21.19|72.82|GJ 396 Valsad / Navsari|20.60|72.93|GJ
400 Mumbai|19.08|72.88|MH 401 Palghar / Vasai|19.40|72.85|MH 402 Raigad|18.50|73.10|MH 403 Goa|15.50|73.83|GA 410 Raigad / Lonavala|18.75|73.40|MH
411 Pune|18.52|73.86|MH 412 Pune rural|18.60|74.00|MH 413 Solapur|17.66|75.91|MH 414 Ahmednagar|19.09|74.74|MH
415 Satara / Sangli|17.68|74.00|MH 416 Kolhapur|16.70|74.24|MH 421 Thane|19.20|73.10|MH 422 Nashik|20.00|73.79|MH
423 Nashik rural|20.30|74.20|MH 424 Dhule|20.90|74.77|MH 425 Jalgaon|21.00|75.56|MH 431 Aurangabad (MH)|19.88|75.34|MH
440 Nagpur|21.15|79.09|MH 441 Nagpur rural|21.15|79.10|MH 442 Chandrapur / Wardha|20.20|79.00|MH 443 Buldhana|20.53|76.18|MH
444 Akola / Amravati|20.70|77.30|MH 445 Yavatmal|20.39|78.12|MH
450 Khandwa|21.82|76.35|MP 451 Khargone|21.82|75.61|MP 452 Indore|22.72|75.86|MP 453 Indore rural / Mhow|22.55|75.76|MP
454 Dhar|22.60|75.30|MP 455 Dewas|22.97|76.05|MP 456 Ujjain|23.18|75.78|MP 457 Ratlam|23.33|75.04|MP 458 Mandsaur / Neemuch|24.07|75.07|MP
460 Betul|21.90|77.90|MP 461 Narmadapuram|22.75|77.72|MP 462 Bhopal|23.26|77.41|MP 464 Vidisha / Raisen|23.52|77.81|MP
465 Shajapur / Rajgarh|23.43|76.27|MP 466 Sehore|23.20|77.08|MP 470 Sagar|23.84|78.74|MP 471 Chhatarpur|24.92|79.58|MP
472 Tikamgarh|24.74|78.83|MP 473 Guna / Shivpuri|24.65|77.31|MP 474 Gwalior|26.22|78.18|MP 475 Datia|25.90|78.40|MP 476 Morena|26.50|78.00|MP
477 Bhind|26.56|78.78|MP 480 Chhindwara|22.06|78.94|MP 481 Balaghat / Mandla|21.80|80.19|MP 482 Jabalpur|23.18|79.95|MP
483 Katni|23.83|80.39|MP 484 Shahdol|23.30|81.36|MP 485 Satna|24.60|80.83|MP 486 Rewa|24.53|81.30|MP 487 Narsinghpur|22.95|79.19|MP
488 Panna|24.72|80.19|MP
490 Durg / Bhilai|21.19|81.28|CG 491 Rajnandgaon|21.10|81.03|CG 492 Raipur|21.25|81.63|CG 493 Mahasamund|21.10|82.10|CG
494 Jagdalpur|19.08|82.02|CG 495 Bilaspur (CG)|22.08|82.15|CG 496 Raigarh|21.90|83.40|CG 497 Ambikapur|23.12|83.20|CG
500 Hyderabad|17.39|78.49|TG 501 Hyderabad rural|17.30|78.30|TG 502 Sangareddy|17.62|78.08|TG 503 Nizamabad|18.67|78.09|TG
504 Adilabad|19.66|78.53|TG 505 Karimnagar|18.44|79.13|TG 506 Warangal|17.97|79.60|TG 507 Khammam|17.25|80.15|TG
508 Nalgonda|17.05|79.27|TG 509 Mahabubnagar|16.74|78.00|TG
515 Anantapur|14.68|77.60|AP 516 Kadapa|14.47|78.82|AP 517 Tirupati / Chittoor|13.63|79.42|AP 518 Kurnool|15.83|78.04|AP
520 Vijayawada|16.51|80.65|AP 521 Krishna|16.40|81.00|AP 522 Guntur|16.31|80.44|AP 523 Ongole|15.50|80.05|AP 524 Nellore|14.44|79.99|AP
530 Visakhapatnam|17.69|83.22|AP 531 Visakhapatnam rural|17.80|83.00|AP 532 Srikakulam|18.30|83.90|AP 533 Kakinada|16.96|82.24|AP
534 Eluru|16.71|81.10|AP 535 Vizianagaram|18.11|83.40|AP
560 Bengaluru|12.97|77.59|KA 561 Chikkaballapur|13.43|77.73|KA 562 Bengaluru rural|13.00|77.30|KA 563 Kolar|13.14|78.13|KA
570 Mysuru|12.30|76.64|KA 571 Chamarajanagar|12.10|76.70|KA 572 Tumakuru|13.34|77.10|KA 573 Hassan|13.00|76.10|KA
574 Dakshina Kannada|13.00|75.00|KA 575 Mangaluru|12.91|74.86|KA 576 Udupi|13.34|74.75|KA 577 Shivamogga|13.93|75.57|KA
580 Hubballi-Dharwad|15.36|75.12|KA 581 Uttara Kannada / Haveri|14.80|75.00|KA 582 Gadag|15.43|75.63|KA 583 Ballari|15.14|76.92|KA
584 Raichur|16.20|77.36|KA 585 Kalaburagi / Bidar|17.33|76.83|KA 586 Vijayapura|16.83|75.71|KA 587 Bagalkot|16.18|75.70|KA
590 Belagavi|15.85|74.50|KA 591 Belagavi rural|16.20|74.80|KA
600 Chennai|13.08|80.27|TN 601 Tiruvallur|13.14|79.91|TN 602 Tiruvallur|13.14|79.91|TN 603 Chengalpattu|12.69|79.98|TN
604 Tindivanam|12.24|79.65|TN 605 Puducherry|11.94|79.81|PY 606 Tiruvannamalai|12.23|79.07|TN 607 Cuddalore|11.75|79.75|TN
608 Chidambaram|11.40|79.69|TN 609 Nagapattinam|10.77|79.84|TN 610 Thiruvarur|10.77|79.64|TN 611 Nagapattinam|10.77|79.84|TN
612 Kumbakonam|10.96|79.38|TN 613 Thanjavur|10.79|79.14|TN 614 Pattukkottai|10.43|79.32|TN 620 Tiruchirappalli|10.79|78.70|TN
621 Perambalur / Ariyalur|11.20|79.00|TN 622 Pudukkottai|10.38|78.82|TN 623 Ramanathapuram|9.37|78.83|TN 624 Dindigul|10.36|77.98|TN
625 Madurai / Theni|9.93|78.12|TN 626 Virudhunagar|9.58|77.96|TN 627 Tirunelveli|8.71|77.76|TN 628 Thoothukudi|8.76|78.13|TN
629 Kanyakumari|8.18|77.41|TN 630 Sivaganga / Karaikudi|10.07|78.78|TN 631 Kanchipuram / Ranipet|12.83|79.70|TN 632 Vellore|12.92|79.13|TN
635 Krishnagiri|12.52|78.21|TN 636 Salem|11.66|78.15|TN 637 Namakkal|11.22|78.17|TN 638 Erode|11.34|77.72|TN 639 Karur|10.96|78.08|TN
641 Coimbatore|11.02|76.96|TN 642 Pollachi / Tiruppur|10.66|77.00|TN 643 Nilgiris|11.41|76.70|TN
670 Kannur|11.87|75.37|KL 671 Kasaragod|12.50|75.00|KL 673 Kozhikode / Wayanad|11.25|75.78|KL 676 Malappuram|11.07|76.07|KL
678 Palakkad|10.78|76.65|KL 679 Palakkad / Malappuram|10.90|76.20|KL 680 Thrissur|10.53|76.21|KL 682 Kochi|9.97|76.28|KL
683 Ernakulam / Aluva|10.10|76.35|KL 685 Idukki|9.85|76.97|KL 686 Kottayam|9.59|76.52|KL 688 Alappuzha|9.50|76.34|KL
689 Pathanamthitta|9.26|76.78|KL 690 Kollam / Alappuzha|9.10|76.60|KL 691 Kollam|8.89|76.61|KL 695 Thiruvananthapuram|8.52|76.94|KL
700 Kolkata|22.57|88.36|WB 711 Howrah|22.59|88.31|WB 712 Hooghly|22.90|88.30|WB 713 Bardhaman / Asansol|23.40|87.40|WB
721 Medinipur|22.42|87.32|WB 722 Bankura|23.23|87.07|WB 723 Purulia|23.33|86.36|WB 731 Birbhum|23.90|87.53|WB 732 Malda|25.00|88.14|WB
733 Dinajpur|25.62|88.12|WB 734 Siliguri / Darjeeling|26.72|88.43|WB 735 Jalpaiguri|26.52|88.72|WB 736 Cooch Behar / Alipurduar|26.32|89.45|WB
737 Gangtok|27.33|88.61|SK 741 Nadia|23.40|88.50|WB 742 Murshidabad|24.10|88.27|WB 743 North 24 Parganas|22.75|88.50|WB
744 Port Blair|11.67|92.74|AN
751 Bhubaneswar|20.30|85.82|OD 752 Khordha / Puri|19.81|85.83|OD 753 Cuttack|20.46|85.88|OD 754 Jagatsinghpur|20.26|86.17|OD
755 Jajpur|20.85|86.33|OD 756 Balasore / Bhadrak|21.49|86.93|OD 757 Mayurbhanj|21.93|86.73|OD 758 Keonjhar|21.63|85.58|OD
759 Dhenkanal / Angul|20.84|85.10|OD 760 Berhampur|19.31|84.79|OD 761 Ganjam|19.50|84.50|OD 762 Kandhamal|20.47|84.23|OD
763 Koraput|18.81|82.71|OD 764 Koraput / Malkangiri|18.50|82.30|OD 765 Rayagada|19.17|83.42|OD 766 Kalahandi|19.91|83.17|OD
767 Bolangir|20.71|83.48|OD 768 Sambalpur|21.47|83.97|OD 769 Rourkela|22.26|84.85|OD 770 Sundargarh|22.12|84.03|OD
781 Guwahati|26.14|91.74|AS 782 Nagaon|26.35|92.68|AS 783 Goalpara / Dhubri|26.17|90.62|AS 784 Tezpur|26.63|92.80|AS
785 Jorhat / Sivasagar|26.75|94.20|AS 786 Dibrugarh / Tinsukia|27.48|94.91|AS 787 North Lakhimpur|27.23|94.10|AS 788 Silchar|24.83|92.78|AS
790 Tawang / Bomdila|27.40|92.40|AR 791 Itanagar|27.10|93.62|AR 792 Tezu / Namsai|27.90|96.10|AR 793 Shillong|25.57|91.88|ML
794 Tura|25.51|90.22|ML 795 Imphal|24.82|93.94|MN 796 Aizawl|23.73|92.72|MZ 797 Dimapur|25.90|93.73|NL 798 Kohima|25.67|94.11|NL
799 Agartala|23.83|91.28|TR
800 Patna|25.59|85.14|BR 801 Patna rural|25.40|85.00|BR 802 Bhojpur / Buxar|25.56|84.66|BR 803 Nalanda|25.20|85.50|BR
804 Jehanabad / Gaya|25.21|84.99|BR 805 Nawada|24.88|85.54|BR 811 Munger|25.38|86.47|BR 812 Bhagalpur|25.24|86.98|BR
813 Banka|24.89|86.92|BR 814 Deoghar / Dumka|24.48|86.69|JH 815 Giridih|24.19|86.30|JH 816 Sahebganj / Pakur|25.24|87.64|JH
821 Rohtas / Sasaram|24.95|84.03|BR 822 Palamu|24.03|84.07|JH 823 Gaya|24.79|85.00|BR 824 Aurangabad (BR)|24.75|84.37|BR
825 Hazaribagh|23.99|85.36|JH 826 Dhanbad|23.80|86.43|JH 827 Bokaro|23.67|86.15|JH 828 Dhanbad rural|23.80|86.40|JH
829 Ramgarh|23.63|85.51|JH 831 Jamshedpur|22.80|86.20|JH 832 Seraikela|22.70|86.10|JH 833 Chaibasa|22.55|85.81|JH
834 Ranchi|23.34|85.31|JH 835 Gumla|23.04|84.54|JH 841 Chhapra / Siwan|25.90|84.50|BR 842 Muzaffarpur|26.12|85.39|BR
843 Sitamarhi|26.60|85.48|BR 844 Hajipur|25.69|85.21|BR 845 Champaran|26.65|84.92|BR 846 Darbhanga|26.15|85.90|BR
847 Madhubani|26.35|86.07|BR 848 Samastipur|25.86|85.78|BR 851 Begusarai|25.42|86.13|BR 852 Saharsa|25.88|86.60|BR
853 Naugachia|25.40|87.10|BR 854 Purnia / Katihar|25.78|87.47|BR 855 Kishanganj|26.10|87.95|BR
"""
_ST = {"JK": "Jammu and Kashmir", "LA": "Ladakh", "UP": "Uttar Pradesh", "RJ": "Rajasthan", "GJ": "Gujarat",
       "MH": "Maharashtra", "GA": "Goa", "MP": "Madhya Pradesh", "CG": "Chhattisgarh", "TG": "Telangana",
       "AP": "Andhra Pradesh", "KA": "Karnataka", "TN": "Tamil Nadu", "PY": "Puducherry", "KL": "Kerala",
       "WB": "West Bengal", "SK": "Sikkim", "AN": "Andaman and Nicobar", "OD": "Odisha", "AS": "Assam",
       "AR": "Arunachal Pradesh", "ML": "Meghalaya", "MN": "Manipur", "MZ": "Mizoram", "NL": "Nagaland",
       "TR": "Tripura", "BR": "Bihar", "JH": "Jharkhand"}
for _m in re.finditer(r"(\d{3}) ([^|]+)\|([\d.]+)\|([\d.]+)\|([A-Z]{2})", _PIN3_REST):
    PIN3.setdefault(_m.group(1), (_m.group(2).strip(), float(_m.group(3)), float(_m.group(4)), _ST[_m.group(5)]))


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
    n = re.sub(r"\s+(city|district|distt?|dist|project|cantt?\.?)$", "", n).strip()
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
