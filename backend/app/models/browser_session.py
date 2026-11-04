"""BrowserSessionRecord ORM model for tracking Playwright browser lifecycle states."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class BrowserLifecycleState(str, enum.Enum):
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    BUSY = "BUSY"
    DISCONNECTED = "DISCONNECTED"
    CRASHED = "CRASHED"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"


class BrowserSessionRecord(Base):
    """Tracks active and historical Playwright browser instances and their health states."""

    __tablename__ = "browser_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    session_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    browser_type: Mapped[str] = mapped_column(String(50), nullable=False, default="chromium")

    state: Mapped[str] = mapped_column(
        String(50), nullable=False, default=BrowserLifecycleState.READY.value, index=True
    )
    is_healthy: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    pages_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    contexts_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    current_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    current_node: Mapped[str | None] = mapped_column(String(100), nullable=True)
    current_step: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_action: Mapped[str | None] = mapped_column(String(100), nullable=True)
    activity_log: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationship
    workflow_run = relationship("WorkflowRun")

    def __repr__(self) -> str:
        return f"<BrowserSessionRecord session_id={self.session_id!r} state={self.state} healthy={self.is_healthy}>"

