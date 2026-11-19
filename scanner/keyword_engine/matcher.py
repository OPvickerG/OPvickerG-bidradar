"""Lexical signals: exact phrase, synonym, fuzzy and negative-context matching."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from rapidfuzz import fuzz

from .model import CategoryModel, Term
from .text import normalise, padded


@dataclass
class LexicalHits:
    phrases: List[str] = field(default_factory=list)
    synonyms: List[str] = field(default_factory=list)
    fuzzy: List[str] = field(default_factory=list)
    negatives: List[str] = field(default_factory=list)


def _contains(p: str, term: Term) -> bool:
    return (" " + term.stem + " ") in p


def lexical_hits(model: CategoryModel, text: str, allow_fuzzy: bool = True) -> LexicalHits:
    """`text` is any field (title, description, document). Returns raw phrase strings that hit."""
    p = padded(text)
    hits = LexicalHits()
    for t in model.phrases:
        if _contains(p, t):
            hits.phrases.append(t.raw)
    for t in model.synonyms:
        if _contains(p, t) and not any(t.stem in ph_stem for ph_stem in
                                       (x.stem for x in model.phrases if x.raw in hits.phrases)):
            hits.synonyms.append(t.raw)
    for t in model.negatives:
        if _contains(p, t):
            hits.negatives.append(t.raw)
    if allow_fuzzy and len(text) <= 1500:
        # Fuzzy catches spelling / spacing variants ("crank-shaft grindng", "cylender head").
        norm = normalise(text)
        for t in model.phrases:
            if t.raw in hits.phrases or len(t.stem.split()) < 2:
                continue
            if fuzz.partial_ratio(normalise(t.raw), norm) >= 90:
                hits.fuzzy.append(t.raw)
    return hits
