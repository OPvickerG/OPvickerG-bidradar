"""Idempotent seeding of categories, keywords and the source registry (safe to run on every start)."""
from __future__ import annotations

from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.schema.models import Category, Keyword, Source
from scanner.keyword_engine.model import CategoryModel, compile_categories, load_seed
from scanner.source_adapters.registry import ADAPTERS

KINDS = ("phrases", "synonyms", "discovery", "objects", "actions", "negatives")
KIND_NAME = {"phrases": "phrase", "synonyms": "synonym", "discovery": "discovery", "objects": "object",
             "actions": "action", "negatives": "negative"}


def seed(db: Session) -> None:
    for c in load_seed():
        cat = db.execute(select(Category).where(Category.code == c["code"])).scalar_one_or_none()
        if cat is None:
            cat = Category(code=c["code"], name=c["name"], description=c.get("description"), enabled=True)
            db.add(cat)
            db.flush()
        existing = {(k.phrase, k.kind) for k in cat.keywords}
        for group in KINDS:
            for item in c.get(group) or []:
                phrase = str(item["phrase"] if isinstance(item, dict) else item)
                weight = float(item.get("weight", 1.0)) if isinstance(item, dict) else 1.0
                key = (phrase, KIND_NAME[group])
                if key not in existing:
                    db.add(Keyword(category_id=cat.id, phrase=phrase, kind=KIND_NAME[group], weight=weight))
                    existing.add(key)
        cat.description = c.get("description")
    for a in ADAPTERS:
        src = db.execute(select(Source).where(Source.key == a.key)).scalar_one_or_none()
        if src is None:
            db.add(Source(key=a.key, name=a.name, base_url=a.base_url, status=a.default_status,
                          enabled=a.implemented and a.default_status in ("enabled", "unreliable"), notes=a.notes))
        else:
            src.name, src.base_url, src.notes = a.name, a.base_url, a.notes
            if not a.implemented:
                src.status, src.enabled = a.default_status, False
    db.flush()


def load_models(db: Session) -> List[CategoryModel]:
    """Build matcher models from the DB (so dashboard keyword edits take effect on the next scan)."""
    seed_meta = {c["code"]: c for c in load_seed()}
    raw = []
    for cat in db.execute(select(Category).order_by(Category.code)).scalars():
        groups = {g: [] for g in KINDS}
        rev = {v: k for k, v in KIND_NAME.items()}
        for k in cat.keywords:
            if k.enabled and k.kind in rev:
                groups[rev[k.kind]].append({"phrase": k.phrase, "weight": k.weight})
        raw.append({"code": cat.code, "name": cat.name, "description": cat.description or "",
                    "enabled": cat.enabled,
                    "supply_only_penalty": seed_meta.get(cat.code, {}).get("supply_only_penalty", True), **groups})
    return compile_categories(raw)
