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
