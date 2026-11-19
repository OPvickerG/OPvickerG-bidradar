"""Concept-based semantic matching.

A tender is semantically relevant to a category when an OBJECT the business works on (e.g. "fuel injection
pump", "DG set", "cylinder head") appears near an ACTION the business performs ("reconditioning",
"overhaul", "facing", "diagnosis"). This captures wording the user never listed:
"Annual maintenance and overhaul of diesel generator sets" -> generator + maintenance/overhaul.

Optionally (EMBEDDING_BACKEND=fastembed) a small sentence-embedding model adds a similarity signal
between the tender text and each category description. It is OFF by default (no model download needed)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from scanner.keyword_engine.model import CategoryModel, Term
from scanner.keyword_engine.text import tokens


@dataclass
class ConceptHit:
    obj: str
    action: str
    distance: int
    strength: float      # 0..1


def _positions(toks: List[str], term: Term) -> List[int]:
    parts = term.stem.split()
    n = len(parts)
    return [i for i in range(len(toks) - n + 1) if toks[i:i + n] == parts]


# Tokens that do not weaken the link between an object and an action ("repair of 125 kva dg set").
_FILLER = {"of", "for", "the", "and", "to", "in", "with", "at", "on", "a", "an", "includ", "includ", "all", "type",
           "kva", "kw", "hp", "cc", "nos", "no", "qty", "unit", "set", "sets", "model", "make", "various", "x"}


SEPARATOR = "zzsepzz"


def _segments(toks: List[str]) -> List[int]:
    """Segment number of each token; list separators (commas, semicolons) start a new segment."""
    out, seg = [], 0
    for t in toks:
        if t == SEPARATOR:
            seg += 1
        out.append(seg)
    return out


def _content_index(toks: List[str]) -> List[int]:
    """Position of each token counting only meaningful tokens (numbers, units and stop-words are skipped)."""
    out, n = [], 0
    for t in toks:
        out.append(n)
        if not (t in _FILLER or t.replace(".", "").isdigit()):
            n += 1
    return out


def best_concept_hit(model: CategoryModel, text: str) -> Optional[ConceptHit]:
    toks = tokens(text)
    if not toks:
        return None
    ci = _content_index(toks)
    sg = _segments(toks)
    best: Optional[ConceptHit] = None
    obj_pos: List[Tuple[Term, int]] = [(o, i) for o in model.objects for i in _positions(toks, o)]
    if not obj_pos:
        return None
    act_pos: List[Tuple[Term, int]] = [(a, i) for a in model.actions for i in _positions(toks, a)]
    for o, oi in obj_pos:
        for a, ai in act_pos:
            if sg[oi] != sg[ai]:
                continue                 # object and action belong to different list items
            d = abs(ci[oi] - ci[ai])
            prox = 1.0 if d <= 4 else 0.75 if d <= 10 else 0.45 if d <= 25 else 0.2
            strength = prox * a.weight * o.weight
            if best is None or strength > best.strength:
                best = ConceptHit(obj=o.raw, action=a.raw, distance=d, strength=strength)
    return best


class EmbeddingScorer:
    """Optional: cosine similarity between text and category descriptions using fastembed (ONNX, CPU)."""

    def __init__(self, categories: List[CategoryModel]):
        from fastembed import TextEmbedding  # imported lazily; optional dependency
        self.model = TextEmbedding("BAAI/bge-small-en-v1.5")
        self.codes = [c.code for c in categories]
        protos = [f"{c.name}. {c.description}. " + ", ".join(t.raw for t in c.phrases[:12]) for c in categories]
        self.proto_vecs = list(self.model.embed(protos))

    def similarities(self, text: str):
        import numpy as np
        v = list(self.model.embed([text[:2000]]))[0]
        out = {}
        for code, p in zip(self.codes, self.proto_vecs):
            out[code] = float(np.dot(v, p) / (np.linalg.norm(v) * np.linalg.norm(p) + 1e-9))
        return out
