"""CircuitBreakerRecord ORM model representing external dependency circuit states."""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class CircuitState(str, enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    # Lowercase aliases
    closed = "CLOSED"
    open = "OPEN"
    half_open = "HALF_OPEN"


class CircuitBreakerRecord(Base):
    """Tracks circuit state, failure counts, and cooldowns for external dependencies."""

    __tablename__ = "circuit_breaker_records"

    dependency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    dependency_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    state: Mapped[str] = mapped_column(
        String(50), nullable=False, default=CircuitState.CLOSED.value, index=True
    )
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_state_change_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    open_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    half_open_max_probes: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    half_open_active_probes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    circuit_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    def __repr__(self) -> str:
        return f"<CircuitBreakerRecord key={self.dependency_key!r} state={self.state}>"

