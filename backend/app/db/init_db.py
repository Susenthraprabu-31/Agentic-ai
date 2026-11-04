"""Database initialisation helper (development only)."""

from app.core.logging import get_logger
from app.db.base import Base, engine

# Import all models so that their tables are registered with Base.metadata
import app.models  # noqa: F401

logger = get_logger(__name__)


async def init_db() -> None:
    """Create all tables that don't already exist.

    This is a convenience helper for development / testing. In production use
    Alembic migrations instead.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("database.tables_created")
