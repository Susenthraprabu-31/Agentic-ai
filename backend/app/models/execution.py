"""WorkflowRun and NodeRun ORM models."""

import enum
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class RunStatus(str, enum.Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"

    # Lowercase aliases for API backwards compatibility
    pending = "PENDING"
    queued = "QUEUED"
    running = "RUNNING"
    recovering = "RECOVERING"
    completed = "COMPLETED"
    failed = "FAILED"
    partially_completed = "PARTIALLY_COMPLETED"
    cancel_requested = "CANCEL_REQUESTED"
    cancelled = "CANCELLED"


class NodeStatus(str, enum.Enum):
    PENDING = "PENDING"
    WAITING = "WAITING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class NodeType(str, enum.Enum):
    input = "input"
    assessor = "assessor"
    tax = "tax"
    normalize = "normalize"
    output = "output"


class WorkflowRun(Base):
    """A single execution of a workflow."""

    __tablename__ = "workflow_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    workflow_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    triggered_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worker_lease_expires_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_recovery_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_heartbeat_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=RunStatus.PENDING.value,
        index=True,
    )
    input_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    workflow: Mapped["Workflow"] = relationship("Workflow")
    workflow_version: Mapped["WorkflowVersion"] = relationship("WorkflowVersion")
    node_runs: Mapped[list["NodeRun"]] = relationship(
        "NodeRun", back_populates="workflow_run", cascade="all, delete-orphan", order_by="NodeRun.created_at"
    )

    @property
    def initiated_by(self) -> uuid.UUID:
        return self.triggered_by

    def __repr__(self) -> str:
        return f"<WorkflowRun id={self.id} status={self.status}>"


class NodeRun(Base):
    """A single node execution within a WorkflowRun."""

    __tablename__ = "node_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    node_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=NodeStatus.PENDING.value,
        index=True,
    )
    input_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    county_source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("county_sources.id", ondelete="SET NULL"), nullable=True
    )
    recipe_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recipe_versions.id", ondelete="SET NULL"), nullable=True
    )

    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    workflow_run: Mapped["WorkflowRun"] = relationship("WorkflowRun", back_populates="node_runs")
    assessor_result: Mapped["AssessorResult | None"] = relationship(
        "AssessorResult", back_populates="node_run", uselist=False, cascade="all, delete-orphan"
    )
    tax_result: Mapped["TaxResult | None"] = relationship(
        "TaxResult", back_populates="node_run", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<NodeRun id={self.id} node_id={self.node_id} status={self.status}>"
