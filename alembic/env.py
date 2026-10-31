"""Alembic environment.

Two things worth noting:

1. The database URL comes from `DocsmindConfig` (i.e. `DOCSMIND_DATABASE_URL` /
   `.env`), not from `alembic.ini`. This means migrations always run against
   whatever the app itself is configured to use — local Docker, Supabase, or
   Neon — with no separate config to keep in sync.
2. We run migrations asynchronously via `asyncpg` (the same driver the app
   uses), rather than pulling in a second, synchronous driver just for
   Alembic.

There's no `target_metadata` (no SQLAlchemy ORM models to autogenerate from —
schema changes are written by hand per revision). That means `alembic revision
--autogenerate` won't work; write new revisions explicitly instead. This is a
deliberate tradeoff: the domain models in `domain/*.py` are plain Pydantic,
not SQLAlchemy ORM classes, since the whole point of the ports/adapters split
is that the domain doesn't know Postgres exists.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from docsmind.config import DocsmindConfig

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def get_url() -> str:
    return DocsmindConfig().database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it (`alembic upgrade head --sql`)."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
