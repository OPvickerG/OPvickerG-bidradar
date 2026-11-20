"""Test harness: every test gets its own throwaway SQLite database (schema from the ORM models; one test
also runs the real Alembic migration). No network access: portals are replaced by tests/fixtures/synthetic.py."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.update({
    "APP_ENV": "test", "SECURE_COOKIES": "false", "EMAIL_PROVIDER": "console", "SESSION_SECRET": "test-secret",
    "PUBLIC_BASE_URL": "http://testserver", "SCANNER_REQUEST_DELAY": "0", "SCANNER_SWEEP_PAGES": "0", "DATABASE_URL": "sqlite:///:memory:",
})

import pytest  # noqa: E402

from core.config import reset_settings  # noqa: E402
from database.session import reset_engine, session_scope  # noqa: E402


@pytest.fixture()
def db_url(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    reset_settings()
    reset_engine()
    from database.schema.models import Base
    from database.session import get_engine
    Base.metadata.create_all(get_engine())
    from database.seed.loader import seed
    with session_scope() as db:
        seed(db)
    from notifications.email_sender.senders import ConsoleSender
    ConsoleSender.outbox.clear()
    yield url
    reset_engine()
    reset_settings()


@pytest.fixture()
def db(db_url):
    from database.session import new_session
    s = new_session()
    yield s
    s.rollback()
    s.close()


@pytest.fixture()
def gem():
    from tests.fixtures.synthetic import MockGem
    return MockGem()


@pytest.fixture()
def run_scan(db_url):
    """Run one full scheduled scan against the given adapters, at an optional fake time."""
    from scanner.scheduler.run import run_once

    def _run(adapters, trigger="manual", now=None, sender=None):
        return run_once(trigger, adapters=adapters, now=now, sender=sender, time_budget_s=120)
    return _run
