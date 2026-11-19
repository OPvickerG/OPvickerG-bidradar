"""Engine / session management with sensible pooling for Cloud Run + serverless Postgres."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from core.config import get_settings

_engine: Optional[Engine] = None
_factory: Optional[sessionmaker] = None


def _normalise_url(url: str) -> str:
    # Accept provider URLs like postgres:// or postgresql:// and use the psycopg3 driver.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_engine() -> Engine:
    global _engine, _factory
    if _engine is None:
        s = get_settings()
        url = _normalise_url(s.database_url)
        if url.startswith("sqlite"):
            _engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}, future=True)

            @event.listens_for(_engine, "connect")
            def _fk_on(dbapi_conn, _):  # enforce FK + WAL for concurrent readers
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA journal_mode=WAL")
                cur.close()
        else:
            _engine = create_engine(url, pool_size=3, max_overflow=2, pool_pre_ping=True, pool_recycle=300,
                                    future=True)
        _factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def reset_engine() -> None:
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _factory = None


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _factory is not None
    s = _factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def new_session() -> Session:
    get_engine()
    assert _factory is not None
    return _factory()
