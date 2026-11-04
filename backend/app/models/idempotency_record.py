"""IdempotencyRecord ORM model tracking operation claims, hashes, and execution targets."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class IdempotencyStatus(str, enum.Enum):
    processing = "processing"
    completed = "completed"
    failed = "failed"
    expired = "expired"


class IdempotencyRecord(Base):
    """Tracks idempotent execution claims across API requests, tasks, and node operations."""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "idempotency_key_hash",
            "operation_type",
            name="uq_idempotency_user_key_op",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    idempotency_key_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    operation_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )
    resource_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    request_payload_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    node_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("node_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=IdempotencyStatus.processing.value, index=True
    )
    response_status_code: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    response_payload: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
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
            f"<IdempotencyRecord id={self.id} op={self.operation_type} "
            f"status={self.status} run_id={self.workflow_run_id}>"
        )

