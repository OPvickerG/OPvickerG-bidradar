"""Text normalisation shared by every matcher: lower-casing, abbreviation repair, light stemming."""
from __future__ import annotations

import re
from typing import List

_REPLACEMENTS = [
    (r"\bd\s*\.?\s*g\s*\.?\s*sets?\b", " dg set "),          # D.G. set, D G Set, DG sets
    (r"\bc\s*\.?\s*r\s*\.?\s*d\s*\.?\s*i\b", " crdi "),       # C.R.D.I
    (r"\bobd\s*[-–]?\s*(ii|2)\b", " obd2 "),                   # OBD-II, OBD 2
    (r"\bf\s*\.?\s*i\s*\.?\s*p\b", " fip "),                   # F.I.P.
    (r"\bgen\s*[-–]?\s*sets?\b", " genset "),                  # Gen-set
    (r"\bre\s*[-–]\s*", " re"),                                # re-boring -> reboring, re-facing -> refacing
    (r"\bcrank\s+shafts?\b", " crankshaft "),
    (r"\bcyl\.?\b", " cylinder "),
    (r"\bmaint\.?\b", " maintenance "),
    (r"\bannual\s+maintenance\s+contracts?\b", " amc "),
    (r"\bcomprehensive\s+maintenance\s+contracts?\b", " cmc "),
    (r"&", " and "),
    (r"\bface\s*plates?\b", " faceplate "),                  # a face plate is a part, not "plate facing" work
    # a kit is a part, not a job: "major repair kit" must not count as the action "repair"
    (r"\b(repair|repairing|overhaul|overhauling|service|servicing)\s+kits?\b", " partskit "),
    # list separators are hard boundaries so phrases/concepts never span two different items
    (r"[,;|\n]+", " zzsepzz "),
]
_NON_WORD = re.compile(r"[^a-z0-9]+")
_STOP = {"of", "the", "for", "and", "to", "in", "on", "with", "a", "an", "at", "by", "or", "as", "per", "is", "be"}


def _stem(tok: str) -> str:
    """Deliberately light, deterministic stemmer. Same function is applied to phrases and to text,
    so only consistency matters: service/servicing/serviced -> 'servic', bore/boring -> 'bor'."""
    if len(tok) <= 3 or tok.isdigit():
        return tok
    t = tok
    if t.endswith("sation") or t.endswith("zation"):
        t = t[:-6] + "s"
    elif t.endswith("ings") and len(t) > 6:
        t = t[:-4]
    elif t.endswith("ing") and len(t) > 5:
        t = t[:-3]
        if len(t) > 3 and t[-1] == t[-2] and t[-1] not in "lsz":
            t = t[:-1]                      # cutting -> cut
    elif t.endswith("ies") and len(t) > 4:
        t = t[:-3] + "y"
    elif t.endswith(("sses", "xes", "zes", "ches", "shes")):
        t = t[:-2]
    elif t.endswith("ed") and len(t) > 4:
        t = t[:-2]
        if len(t) > 3 and t[-1] == t[-2] and t[-1] not in "lsz":
            t = t[:-1]
    elif t.endswith("s") and not t.endswith("ss") and len(t) > 3:
        t = t[:-1]
    if t.endswith("e") and len(t) > 3:
        t = t[:-1]                          # service -> servic ; engine -> engin ; bore -> bor
    return t


def normalise(text: str) -> str:
    t = " " + (text or "").lower() + " "
    for pat, rep in _REPLACEMENTS:
        t = re.sub(pat, rep, t)
    t = _NON_WORD.sub(" ", t)
    return " ".join(t.split())


def tokens(text: str, drop_stopwords: bool = False) -> List[str]:
    toks = [_stem(w) for w in normalise(text).split()]
    if drop_stopwords:
        toks = [w for w in toks if w not in _STOP]
    return toks


def stem_phrase(phrase: str) -> str:
    return " ".join(tokens(phrase))


def padded(text: str) -> str:
    """Stemmed token string padded with spaces so ' phrase ' lookups respect word boundaries."""
    return " " + " ".join(tokens(text)) + " "
