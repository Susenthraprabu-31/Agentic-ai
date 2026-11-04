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


engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DATABASE_ECHO,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# Convenience alias; models can also reference Base.metadata directly.
metadata = Base.metadata
