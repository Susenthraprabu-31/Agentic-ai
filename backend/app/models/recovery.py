"""WorkflowRecoveryRecord ORM model representing workflow recovery attempts and outcomes."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class RecoveryStatus(str, enum.Enum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    # Lowercase aliases
    started = "STARTED"
    succeeded = "SUCCEEDED"
    failed = "FAILED"
    cancelled = "CANCELLED"


class WorkflowRecoveryRecord(Base):
    """Audit log and status of recovery attempts for interrupted/orphaned workflow runs."""

    __tablename__ = "workflow_recovery_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    previous_worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recovery_worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=RecoveryStatus.STARTED.value, index=True
    )
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    workflow_run: Mapped["WorkflowRun"] = relationship("WorkflowRun")

    def __repr__(self) -> str:
        return f"<WorkflowRecoveryRecord id={self.id} run_id={self.workflow_run_id} status={self.status}>"

