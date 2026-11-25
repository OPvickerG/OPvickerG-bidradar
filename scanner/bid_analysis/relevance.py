"""Relevance scoring (0-100) with a human-readable explanation.

Bands (configurable threshold in settings):
  90-100 very strong | 75-89 strong | 50-74 potentially relevant | 25-49 weak | 0-24 irrelevant
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Tuple, Dict, List, Optional

from scanner.keyword_engine.matcher import lexical_hits
from scanner.keyword_engine.model import CategoryModel, default_categories
from scanner.keyword_engine.text import normalise, tokens
from scanner.semantic_matching.concepts import best_concept_hit

SERVICE_STEMS = {"repair", "overhaul", "recondition", "servic", "maintenanc", "calibration", "calibrat", "test",
                 "bor", "rebor", "grind", "fac", "refac", "resurfac", "diagnostic", "diagnosi", "scan", "amc", "cmc",
                 "rectification", "job", "machin", "hon", "lapp", "rebuild", "troubleshoot"}
_SUPPLY_RX = re.compile(r"\b(supply of|procurement of|purchase of|providing of new|new)\b")
_EQUIPMENT_RX = re.compile(
    r"\b(grinding|boring|honing|testing|test|calibration|diagnostic|diagnostics|scanning|facing|lapping|injector)\s+"
    r"(machineunit|machine|machines|equipment|bench|benches|stand|tool|tools|kit|kits|scanner|tester|rig|machinery)\b")
_JOB_WORK_RX = re.compile(r"\b(job work|repair|overhaul|servicing|maintenance|amc|cmc|reconditioning)\b")

# The business is road vehicles and DG sets. GeM files aircraft, ship and gun work under "Repair and Overhauling
# Service - built up trucks / cars", so the item itself (after the GeM category label) decides.
_NOT_ROAD_RX = re.compile(
    r"\b(helicopters?|aircrafts?|aero ?engines?|aeroengines?|avionics?|airframes?|aviation|mi 17|mi 8|chetak|cheetah|"
    r"alh|dhruv|uavs?|drones?|microlite|microlight|ships?|vessels?|boats?|marine crafts?|marine engines?|watercrafts?|"
    r"lcm|landing crafts?|locomotives?|rolling stock|railway coach(?:es)?|bofors|howitzers?|artillery|155mm|105mm|"
    r"field guns?|tanks? t ?72|t ?90|bmp ?2)\b")
_ROAD_VEHICLE_RX = re.compile(
    r"\b(refuell?ers?|bowsers?|trucks?|lorry|lorries|vehicles?|bus|buses|minibus(?:es)?|vans?|cars?|jeeps?|gypsy|"
    r"ambulances?|tractors?|tippers?|tankers?|motor ?cycles?|scooters?|suvs?|muvs?|pick ?up|dg sets?|d g sets?|"
    r"gensets?|generators?|generating sets?)\b")
# Hiring, leasing or insuring a vehicle / DG set is not a repair job.
_HIRE_RX = re.compile(r"\b(hiring|hire|hired|on rent|rental|leasing|lease|insurance|all inclusive|outsourcing|"
                      r"manpower)\b")
_REPAIR_WORDS_RX = re.compile(r"\b(repairs?|repairing|overhaul\w*|recondition\w*|rectification|servicing|"
                              r"maintenance|amc|cmc|breakdown|denting)\b")
_VEHICLE_LABEL_RX = re.compile(r"repair and overhauling service (?:built up trucks|cars|vehicles?|buses|trucks)")
_REPAIR_CATEGORY_RX = re.compile(r"(repair and overhauling service|customized amc/cmc|repair, maintenance)")
_GEM_LABEL_RX = re.compile(r"^[^;]{0,120}? - [^;]{0,80};")


def item_text(title: str, description: str = "") -> str:
    """The item being bought, without GeM's category label ('Repair and Overhauling Service - built up trucks;')."""
    parts = []
    for t in (title or "", description or ""):
        parts.append(_GEM_LABEL_RX.sub(" ", t, count=1))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", " ".join(parts).lower()).split())


def scope_penalties(title: str, description: str = "") -> List[Tuple[int, str]]:
    """(score cap, reason) for work outside the business, however strong the keyword match."""
    item = item_text(title, description)
    full = " ".join(re.sub(r"[^a-z0-9]+", " ", f"{title or ''} {description or ''}".lower()).split())
    in_scope = _ROAD_VEHICLE_RX.search(_VEHICLE_LABEL_RX.sub(" ", full))     # ignore GeM's generic vehicle label
    out = []
    if _NOT_ROAD_RX.search(item) and not in_scope:
        out.append((20, "aircraft / ship / rail / weapon work, not road vehicles or DG sets"))
    repair_category = _REPAIR_CATEGORY_RX.match((title or "").strip().lower())   # the buyer chose a repair category
    if _HIRE_RX.search(item) and not _REPAIR_WORDS_RX.search(item) and not repair_category:
        out.append((30, "hiring / leasing / insurance, not a repair job"))
    return out


def band(score: int) -> str:
    if score >= 90:
        return "very strong"
    if score >= 75:
        return "strong"
    if score >= 50:
        return "potentially relevant"
    if score >= 25:
        return "weak"
    return "irrelevant"


@dataclass
class CategoryScore:
    code: str
    name: str
    score: int
    matched_phrases: List[str] = field(default_factory=list)
    explanation: str = ""
    breakdown: Dict[str, float] = field(default_factory=dict)


@dataclass
class RelevanceResult:
    score: int
    best_category: Optional[str]
    explanation: str
    categories: List[CategoryScore]

    @property
    def band(self) -> str:
        return band(self.score)


def _is_supply_only(main_text: str, procurement_type: Optional[str]) -> bool:
    toks = set(tokens(main_text))
    has_service_action = bool(toks & SERVICE_STEMS)
    if has_service_action:
        return False
    return procurement_type == "product" or bool(_SUPPLY_RX.search(normalise(main_text)))


SEP = " zzsepzz "     # hard boundary: phrases and concepts must not span title -> description


def join_fields(title: Optional[str], description: Optional[str]) -> str:
    t, d = (title or "").strip(), (description or "").strip()
    if not d or normalise(d) == normalise(t) or normalise(d) in normalise(t):
        return t
    return t + SEP + d


# laboratory / process "generators" are not DG sets: 'Calibration of Function Generator', 'PSA oxygen generator'
_INSTRUMENT_GEN_RX = re.compile(r"(?i)\b(signal|function|pulse|waveform|arbitrary|rf|noise|frequency|sweep|pattern|tone|"
                                r"clock|test|smoke|fog|foam|ion|ozone|oxygen|nitrogen|hydrogen|steam|x-?ray|ultrasonic|"
                                r"ultrasound|word|report|qr|code|number|random|vapou?r|bubble|aerosol)\s+generators?\b")


def _strip_instrument_generators(text: str) -> str:
    return _INSTRUMENT_GEN_RX.sub(" instrument ", text or "")


def score_category(model: CategoryModel, title: str, description: str = "", document_text: str = "",
                   procurement_type: Optional[str] = None, embedding_sim: Optional[float] = None) -> CategoryScore:
    title, description = _strip_instrument_generators(title), _strip_instrument_generators(description)
    document_text = _strip_instrument_generators(document_text) if document_text else document_text
    main = join_fields(title, description)
    lex = lexical_hits(model, main)
    con = best_concept_hit(model, main)
    b: Dict[str, float] = {}
    why: List[str] = []

    if lex.phrases:
        b["phrase"] = 55 + min(16, 8 * (len(lex.phrases) - 1))
        why.append("phrase " + ", ".join(f"'{p}'" for p in lex.phrases[:3]))
    if lex.synonyms:
        b["synonym"] = 22 + min(12, 6 * (len(lex.synonyms) - 1))
        why.append("related term " + ", ".join(f"'{p}'" for p in lex.synonyms[:3]))
    if con:
        b["semantic"] = round(58 * con.strength, 1)
        if con.distance <= 4 and con.strength >= 0.6:
            b["semantic_tight"] = 17
        why.append(f"service concept: '{con.obj}' + '{con.action}' ({con.distance} words apart)")
    if lex.fuzzy and not lex.phrases:
        b["fuzzy"] = 15
        why.append("close spelling of " + ", ".join(f"'{p}'" for p in lex.fuzzy[:2]))
    if sum(1 for k in ("phrase", "synonym", "semantic") if b.get(k)) >= 2:
        b["multi_signal"] = 10

    main_signal = any(b.get(k) for k in ("phrase", "synonym", "semantic"))
    doc_part = 0.0
    if document_text:
        dl = lexical_hits(model, document_text[:60000], allow_fuzzy=False)
        dc = best_concept_hit(model, document_text[:20000])
        doc_part = (15 if dl.phrases else 0) + (8 if dl.synonyms else 0) + (12 * dc.strength if dc else 0)
        doc_part = min(25.0, doc_part)
        if doc_part:
            terms = (dl.phrases + dl.synonyms)[:3]
            why.append("tender document mentions " + (", ".join(f"'{t}'" for t in terms) if terms
                                                       else f"'{dc.obj}' with '{dc.action}'"))
        if dl.negatives and not main_signal:
            b["doc_negative"] = -10
        b["document"] = round(doc_part, 1)

    if embedding_sim is not None and embedding_sim > 0.55:
        b["embedding"] = round(min(15.0, (embedding_sim - 0.55) * 60), 1)

    total = sum(v for v in b.values())
    penalties: List[str] = []
    if lex.negatives:
        total -= 45
        penalties.append("unrelated context " + ", ".join(f"'{n}'" for n in lex.negatives[:3]))
    scope_caps = scope_penalties(title, description)
    if procurement_type == "product" and not _REPAIR_WORDS_RX.search(item_text(title, description)):
        # a GeM goods purchase with no repair wording ('Grinding disc 4 inch' in a plywood order) is not service work
        scope_caps.append((35, "goods purchase, no repair or service work"))
    for _, reason in scope_caps:
        penalties.append(reason)
    norm_main = normalise(main)
    if model.supply_only_penalty and _is_supply_only(main, procurement_type):
        total -= 25
        penalties.append("appears to be purchase of goods, not a repair/service job")
    equipment_purchase = bool(_EQUIPMENT_RX.search(norm_main)) and not _JOB_WORK_RX.search(norm_main)
    if equipment_purchase:
        penalties.append("appears to be purchase of equipment/tools, not a service")

    if not main_signal:
        cap = 55 if doc_part else 45
        total = min(total, cap)
    if equipment_purchase:
        total = min(total, 35)
    for cap_at, _ in scope_caps:
        total = min(total, cap_at)
    score = int(max(0, min(100, round(total))))
    matched = list(dict.fromkeys(lex.phrases + lex.synonyms + lex.fuzzy +
                                 ([f"{con.obj} + {con.action}"] if con else [])))
    expl = (f"{band(score).capitalize()} match for {model.name}: " + "; ".join(why)) if why else \
        f"No meaningful signal for {model.name}"
    if penalties:
        expl += ". Reduced because of " + "; ".join(penalties)
    return CategoryScore(code=model.code, name=model.name, score=score, matched_phrases=matched,
                         explanation=expl + ".", breakdown=b)


_WORD = re.compile(r"[A-Za-z]{6,}")
_ENGLISH_GUARD = {"condition", "conditions", "conditioning", "position", "station", "section", "selection",
                  "inspection", "injection", "direction", "protection", "construction", "operation"}
_vocab_cache: Dict[int, Tuple[frozenset, list]] = {}


def _vocab(cats: List[CategoryModel]) -> frozenset:
    key = hash(tuple((c.code, len(c.phrases), len(c.synonyms), len(c.objects), len(c.actions)) for c in cats))
    if key not in _vocab_cache:
        words = set()
        for c in cats:
            for t in c.phrases + c.synonyms + c.objects + c.actions:
                words.update(w for w in re.findall(r"[a-z]+", t.raw.lower()) if len(w) >= 5)
        _vocab_cache[key] = (frozenset(words), sorted(words))
    return _vocab_cache[key][0]


def correct_spelling(text: str, cats: List[CategoryModel]) -> Tuple[str, List[Tuple[str, str]]]:
    """Fix obvious misspellings of domain words ('injecton' -> 'injection'). Conservative: only words of 6+
    letters that are not already domain words, and only when similarity >= 88%."""
    from rapidfuzz import fuzz, process
    vocab = _vocab(cats)
    fixes: List[Tuple[str, str]] = []

    def fix(m):
        w = m.group(0)
        lw = w.lower()
        if lw in vocab:
            return w
        hit = process.extractOne(lw, vocab, scorer=fuzz.ratio, score_cutoff=88)
        if hit and lw not in hit[0] and hit[0] not in lw:     # never turn 'condition' into 'recondition'
            if lw in _ENGLISH_GUARD:
                return w
            fixes.append((w, hit[0]))
            return hit[0]
        return w
    return _WORD.sub(fix, text or ""), fixes


def score_tender(title: str, description: str = "", document_text: str = "",
                 procurement_type: Optional[str] = None, categories: Optional[List[CategoryModel]] = None,
                 embedder=None) -> RelevanceResult:
    cats = [c for c in (categories or default_categories()) if c.enabled]
    base = _score_tender(title, description, document_text, procurement_type, cats, embedder)
    if base.score >= 75:
        return base                       # already an alert-level match: spelling correction cannot matter
    ct, f1 = correct_spelling(title, cats)
    cd, f2 = correct_spelling(description or "", cats)
    if f1 or f2:
        fixed = _score_tender(ct, cd, document_text, procurement_type, cats, embedder)
        if fixed.score - 5 > base.score:
            note = "after correcting spelling " + ", ".join(f"'{a}'→'{b}'" for a, b in (f1 + f2)[:3])
            fixed.score -= 5
            for c in fixed.categories:
                if c.code == fixed.best_category:
                    c.score = fixed.score
                    c.breakdown["spelling_correction"] = -5
                    c.explanation = c.explanation.rstrip(".") + f" ({note})."
            fixed.explanation = fixed.explanation.replace(".", f" ({note}).", 1) if "." in fixed.explanation \
                else fixed.explanation + f" ({note})"
            return fixed
    return base


def _specificity(c: CategoryScore) -> int:
    """Tie-breaker: a match on 'cylinder head' is more specific than one on 'vehicle'."""
    return max([len(m.split(" + ")[0].split()) for m in c.matched_phrases if " + " in m] + [0])


def _score_tender(title: str, description: str, document_text: str, procurement_type: Optional[str],
                  cats: List[CategoryModel], embedder) -> RelevanceResult:
    sims = {}
    if embedder is not None:
        try:
            sims = embedder.similarities(f"{title}. {description}")
        except Exception:  # embeddings are optional; never fail a scan because of them
            sims = {}
    scored = [score_category(c, title, description, document_text, procurement_type, sims.get(c.code)) for c in cats]
    scored.sort(key=lambda s: (s.score, _specificity(s)), reverse=True)
    best = scored[0] if scored else None
    if not best or best.score <= 0:
        return RelevanceResult(score=0, best_category=None, explanation="No business category matched.",
                               categories=scored)
    others = [s for s in scored[1:] if s.score >= 50]
    expl = best.explanation
    if others:
        expl += " Also relevant to: " + ", ".join(f"{s.name} ({s.score})" for s in others) + "."
    return RelevanceResult(score=best.score, best_category=best.code, explanation=expl, categories=scored)
