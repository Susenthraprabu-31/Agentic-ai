"""RetryAttempt ORM model tracking transient failure retries."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class RetryAttemptStatus(str, enum.Enum):
    retrying = "retrying"
    succeeded = "succeeded"
    exhausted = "exhausted"
    aborted = "aborted"


class RetryAttempt(Base):
    """Persisted record of an operation retry attempt."""

    __tablename__ = "retry_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    node_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("node_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    operation_name: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    operation_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    attempt_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    error_category: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )
    error_type: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    error_message: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    retry_delay_seconds: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=RetryAttemptStatus.retrying.value, index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    workflow_run: Mapped["WorkflowRun | None"] = relationship(
        "WorkflowRun", foreign_keys=[workflow_run_id]
    )
    node_run: Mapped["NodeRun | None"] = relationship(
        "NodeRun", foreign_keys=[node_run_id]
    )

    def __repr__(self) -> str:
        return (
            f"<RetryAttempt id={self.id} op={self.operation_name} "
            f"attempt={self.attempt_number} status={self.status}>"
        )

