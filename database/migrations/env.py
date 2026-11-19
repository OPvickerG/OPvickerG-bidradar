"""Alembic environment: URL from DATABASE_URL (Secret Manager in the cloud), models from database.schema."""
from __future__ import annotations

from alembic import context

from database.schema.models import Base
from database.session import get_engine

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    from core.config import get_settings
    from database.session import _normalise_url
    context.configure(url=_normalise_url(get_settings().database_url), target_metadata=target_metadata,
                      literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata,
                          render_as_batch=conn.dialect.name == "sqlite", compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
