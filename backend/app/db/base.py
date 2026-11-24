"""SQLAlchemy declarative base, async engine, and metadata.

Do NOT import models here — models import Base from this module.
That separation prevents circular imports.
"""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""

    pass


def _engine_kwargs() -> dict:
    url = settings.DATABASE_URL
    if url.startswith("sqlite"):
        return {
            "echo": settings.DATABASE_ECHO,
            "connect_args": {"check_same_thread": False},
        }
    return {
        "echo": settings.DATABASE_ECHO,
        "pool_pre_ping": True,
        "pool_size": 10,
        "max_overflow": 20,
    }


engine: AsyncEngine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs())

# Convenience alias; models can also reference Base.metadata directly.
metadata = Base.metadata
