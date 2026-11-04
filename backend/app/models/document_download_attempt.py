"""DocumentDownloadAttemptRecord ORM model for auditing granular download attempts."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class DownloadAttemptStatus(str, enum.Enum):
    in_progress = "in_progress"
    completed = "completed"
    range_resumed = "range_resumed"
    failed = "failed"
    aborted = "aborted"


class DocumentDownloadAttemptRecord(Base):
    """Tracks each granular download attempt, bytes transferred, HTTP Range resume details, and errors."""

    __tablename__ = "document_download_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    document_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_artifacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=DownloadAttemptStatus.in_progress.value, index=True
    )

    downloaded_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    range_resumed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resumed_from_byte: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    document_artifact = relationship("DocumentArtifact", back_populates="download_attempts_history")

    def __repr__(self) -> str:
        return f"<DocumentDownloadAttemptRecord id={self.id} doc={self.document_artifact_id} attempt={self.attempt_number} status={self.status}>"

