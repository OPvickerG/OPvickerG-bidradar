"""Compiled category model used by all matchers (built from the DB or the seed YAML)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

import yaml

from .text import stem_phrase

SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "database", "seed", "categories.yaml")


@dataclass
class Term:
    raw: str
    stem: str
    weight: float = 1.0


@dataclass
class CategoryModel:
    code: str
    name: str
    description: str = ""
    enabled: bool = True
    supply_only_penalty: bool = True
    phrases: List[Term] = field(default_factory=list)
    synonyms: List[Term] = field(default_factory=list)
    discovery: List[str] = field(default_factory=list)
    objects: List[Term] = field(default_factory=list)
    actions: List[Term] = field(default_factory=list)
    negatives: List[Term] = field(default_factory=list)


def _terms(values: Iterable, weight: float = 1.0) -> List[Term]:
    out, seen = [], set()
    for v in values or []:
        if isinstance(v, dict):
            raw, w = v["phrase"], float(v.get("weight", weight))
        else:
            raw, w = str(v), weight
        st = stem_phrase(raw)
        if st and st not in seen:
            seen.add(st)
            out.append(Term(raw=raw, stem=st, weight=w))
    return out


# Weak actions contribute less: 'service' / 'maintenance' / 'test' appear in many unrelated tenders.
WEAK_ACTIONS = {"servic", "maintenanc", "test", "amc", "cmc", "rectification"}


def load_seed(path: str = SEED_PATH) -> List[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)["categories"]


def compile_categories(raw: List[dict]) -> List[CategoryModel]:
    out = []
    for c in raw:
        actions = _terms(c.get("actions"))
        for a in actions:
            if a.stem in WEAK_ACTIONS:
                a.weight = min(a.weight, 0.85)
        out.append(CategoryModel(
            code=c["code"], name=c["name"], description=c.get("description", ""),
            enabled=c.get("enabled", True), supply_only_penalty=c.get("supply_only_penalty", True),
            phrases=_terms(c.get("phrases")), synonyms=_terms(c.get("synonyms")),
            discovery=list(dict.fromkeys(str(d["phrase"] if isinstance(d, dict) else d).strip().lower()
                                         for d in (c.get("discovery") or []))),
            objects=_terms(c.get("objects")), actions=actions, negatives=_terms(c.get("negatives"))))
    return out


_cached: Optional[List[CategoryModel]] = None


def default_categories() -> List[CategoryModel]:
    global _cached
    if _cached is None:
        _cached = compile_categories(load_seed())
    return _cached


def categories_by_code(models: List[CategoryModel]) -> Dict[str, CategoryModel]:
    return {m.code: m for m in models}
