"""Alembic async environment configuration."""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# ---------------------------------------------------------------------------
# Import all models so their tables are registered with Base.metadata.
# This must come before `target_metadata` is set.
# ---------------------------------------------------------------------------
from app.db.base import Base  # noqa: F401 — registers Base
import app.models  # noqa: F401 — registers all ORM tables

# ---------------------------------------------------------------------------
# Alembic config
# ---------------------------------------------------------------------------
config = context.config

# Interpret the config file for Python logging (if present).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from app.core.config import settings

# Override the sqlalchemy.url from settings / environment — never hardcode credentials.
_db_url = os.environ.get("DATABASE_URL") or settings.DATABASE_URL or config.get_main_option("sqlalchemy.url")
config.set_main_option("sqlalchemy.url", _db_url)

target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    """Run migrations without an active DB connection (generates SQL script)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):  # type: ignore[no-untyped-def]
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_num_length=64,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations using an async engine."""
    connectable = create_async_engine(
        config.get_main_option("sqlalchemy.url"),  # type: ignore[arg-type]
        echo=False,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
