"""DocumentArtifact ORM model for retrieved and validated public-record documents."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class DocumentStatus(str, enum.Enum):
    pending = "pending"
    discovered = "discovered"
    queued = "queued"
    downloading = "downloading"
    validating = "validating"
    downloaded = "downloaded"
    validated = "validated"
    completed = "completed"
    retry_pending = "retry_pending"
    failed = "failed"
    cancelled = "cancelled"
    duplicate = "duplicate"


class DocumentArtifact(Base):
    """Stores metadata and storage pointers for retrieved county property documents (PDFs)."""

    __tablename__ = "document_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    node_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("node_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    property_record_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True, index=True
    )
    source_configuration_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_configurations.id", ondelete="SET NULL"), nullable=True
    )

    document_type: Mapped[str] = mapped_column(String(100), nullable=False, default="deed")
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_page_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False, default="application/pdf")
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Basic PDF Structural Metadata
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pdf_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pdf_author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    document_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recording_number: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=DocumentStatus.pending.value, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_artifacts.id", ondelete="SET NULL"), nullable=True
    )

    # Resilience & Resumption Fields (Phase 3D.4)
    etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(255), nullable=True)
    downloaded_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    download_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    temp_filepath: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    # Relationships
    workflow_run = relationship("WorkflowRun")
    node_run = relationship("NodeRun")
    source_configuration = relationship("SourceConfiguration")
    duplicate_of = relationship("DocumentArtifact", remote_side=[id])
    download_attempts_history = relationship(
        "DocumentDownloadAttemptRecord",
        back_populates="document_artifact",
        cascade="all, delete-orphan",
        order_by="DocumentDownloadAttemptRecord.attempt_number",
    )

    def __repr__(self) -> str:
        return f"<DocumentArtifact id={self.id} filename={self.original_filename!r} status={self.status}>"

