"""Requirement 14: database persistence (survives restarts, unique identities, real Alembic migration)."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError

from core.config import reset_settings
from core.timeutil import utcnow
from database.schema.models import Base, Category, Keyword, Source, Tender, TenderEvent
from database.session import reset_engine, session_scope


def test_data_survives_engine_restart(db_url):
    with session_scope() as db:
        db.add(Tender(source_key="gem_bidplus", external_id="1", title="Repair of DG set", details={"a": 1}))
    reset_engine()                                    # simulates a new container / process
    with session_scope() as db:
        t = db.execute(select(Tender)).scalar_one()
        assert t.title == "Repair of DG set" and t.details == {"a": 1} and t.first_seen_at is not None


def test_unique_tender_identity_enforced(db_url):
    with session_scope() as db:
        db.add(Tender(source_key="gem_bidplus", external_id="1", title="a", details={}))
    with pytest.raises(IntegrityError):
        with session_scope() as db:
            db.add(Tender(source_key="gem_bidplus", external_id="1", title="b", details={}))


def test_history_is_append_only_and_cascades(db_url):
    with session_scope() as db:
        t = Tender(source_key="gem_bidplus", external_id="2", title="x", details={})
        db.add(t)
        db.flush()
        db.add(TenderEvent(tender_id=t.id, event_type="deadline_changed", old_value="a", new_value="b"))
        db.add(TenderEvent(tender_id=t.id, event_type="deadline_changed", old_value="b", new_value="c"))
    with session_scope() as db:
        assert [e.new_value for e in db.execute(select(TenderEvent).order_by(TenderEvent.id)).scalars()] == ["b", "c"]


def test_seed_is_idempotent(db_url):
    from database.seed.loader import seed
    with session_scope() as db:
        before = (db.query(Category).count(), db.query(Keyword).count(), db.query(Source).count())
        seed(db)
        seed(db)
        after = (db.query(Category).count(), db.query(Keyword).count(), db.query(Source).count())
    assert before == after and before[0] == 6 and before[2] >= 3


def test_alembic_migration_builds_the_full_schema(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    reset_settings()
    reset_engine()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "database", "migrations"))
    command.upgrade(cfg, "head")
    tables = set(inspect(create_engine(url)).get_table_names())
    assert set(Base.metadata.tables) <= tables and "alembic_version" in tables
    reset_engine()
    reset_settings()


# --- database transfer budget (seen 25 Nov 2026: the free 5 GB/month was used up in six days) -------------------
def test_document_text_is_stored_apart_from_the_tender_row(gem, run_scan):
    from sqlalchemy import select
    from database.schema.models import Tender, TenderText
    from tests.fixtures.synthetic import gem_bid_pdf, gem_doc
    gem.docs = [gem_doc(7900001, "Repair and Overhauling Service - DG Set; Diesel Generator; Yes; Buyer Premises")]
    gem.pdfs["7900001"] = gem_bid_pdf("GEM/2026/B/7900001", pin="133001")
    run_scan({"gem_bidplus": gem.adapter()})
    with session_scope() as db:
        t = db.execute(select(Tender).where(Tender.external_id == "7900001")).scalar_one()
        assert "doc_text" not in t.details and "doc_consignee" not in t.details     # listing rows stay small
        row = db.get(TenderText, t.id)
        assert row is not None and "Bid Number" in row.doc_text
        assert t.details["geo"]["place"] == "Ambala"                                 # location still read from it


def test_migration_moves_old_document_text_out_of_details(tmp_path, monkeypatch):
    import json
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    url = f"sqlite:///{tmp_path / 'old.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    reset_settings()
    reset_engine()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "database", "migrations"))
    command.upgrade(cfg, "0001")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("INSERT INTO tenders (id, source_key, external_id, title, status, details, relevance_score, "
                       "is_relevant, watched, first_seen_at, last_seen_at) VALUES (1, 'gem_bidplus', 'X1', 'Repair', "
                       "'active', :d, 80, 1, 0, '2026-11-20', '2026-11-20')"),
                  {"d": json.dumps({"doc_text": "long text", "doc_consignee": "", "geo": {"near": "yes"}})})
    command.upgrade(cfg, "head")
    with eng.begin() as c:
        assert json.loads(c.execute(text("SELECT details FROM tenders")).scalar()) == {"geo": {"near": "yes"}}
        assert tuple(c.execute(text("SELECT tender_id, doc_text, doc_consignee FROM tender_texts")).one()) == \
            (1, "long text", "")
    reset_engine()
    reset_settings()


def test_older_scoring_code_never_rescores_over_newer_rules(db):
    from backend import settings_service as cfg
    from scanner.discovery import pipeline
    cfg.put(db, "scoring", {"fingerprint": "from-newer-code", "version": pipeline.SCORING_VERSION + 1})
    assert pipeline.rescore_if_rules_changed(db) == 0
    assert cfg.get(db, "scoring")["fingerprint"] == "from-newer-code"
    cfg.put(db, "geo_version", {"key": f"{int(pipeline.GEO_VERSION) + 1}:1:2:3"})
    assert pipeline.relocate_if_changed(db) == 0
