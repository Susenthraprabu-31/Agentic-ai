"""First-run database bootstrap for desktop SQLite installs."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import Base, engine

import app.models  # noqa: F401

logger = get_logger(__name__)

_PG_ONLY_SERVER_DEFAULT_FRAGMENTS = ("gen_random_uuid",)


def _strip_postgres_only_server_defaults() -> None:
    """Remove DDL fragments SQLite cannot compile (ORM create_all)."""
    for table in Base.metadata.tables.values():
        for column in table.columns:
            default = column.server_default
            if default is None:
                continue
            try:
                rendered = str(getattr(default, "arg", default))
            except Exception:
                rendered = str(default)
            if any(frag in rendered for frag in _PG_ONLY_SERVER_DEFAULT_FRAGMENTS):
                column.server_default = None


async def bootstrap_desktop_database() -> None:
    """Create schema via metadata when using SQLite (avoids PG-only Alembic defaults)."""
    if not settings.DESKTOP_MODE:
        return
    if not settings.DATABASE_URL.startswith("sqlite"):
        return

    _strip_postgres_only_server_defaults()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA foreign_keys=ON"))
        except Exception as exc:
            logger.debug("SQLite pragma note: %s", exc)

    db_path = settings.DATABASE_URL.replace("sqlite+aiosqlite:///", "")
    logger.info("desktop.database.ready", path=db_path, exists=Path(db_path).is_file())