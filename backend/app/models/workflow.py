"""Workflow and WorkflowVersion ORM models."""

import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class Workflow(Base):
    """Represents a named, reusable title search workflow."""

    __tablename__ = "workflows"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    county_source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("county_sources.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    # Relationships
    versions: Mapped[list["WorkflowVersion"]] = relationship(
        "WorkflowVersion",
        back_populates="workflow",
        cascade="all, delete-orphan",
        order_by="desc(WorkflowVersion.version_number)",
    )

    def __repr__(self) -> str:
        return f"<Workflow id={self.id} name={self.name!r}>"


class WorkflowVersion(Base):
    """Immutable snapshot of a workflow definition at a point in time."""

    __tablename__ = "workflow_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    workflow: Mapped["Workflow"] = relationship("Workflow", back_populates="versions")

    def __repr__(self) -> str:
        return f"<WorkflowVersion id={self.id} workflow_id={self.workflow_id} v={self.version_number}>"
