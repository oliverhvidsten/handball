"""Alembic environment. Migrations are raw-SQL (op.execute) over Postgres-specific
DDL (enums, views), so there is no target_metadata / autogenerate. The connection
URL comes from handball.db so dev, tests, and prod share one source of truth."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from handball.db import db_url, get_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # raw-SQL migrations; no autogenerate


def run_migrations_offline() -> None:
    context.configure(
        url=db_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = get_engine(poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
