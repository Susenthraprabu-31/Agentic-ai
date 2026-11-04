"""AssessorResult and TaxResult ORM models."""

import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class AssessorResult(Base):
    """Parsed assessor data for a single node run."""

    __tablename__ = "assessor_results"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    node_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    parcel_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    property_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessed_value: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    land_value: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    improvement_value: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    tax_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    screenshot_s3_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationship
    node_run: Mapped["NodeRun"] = relationship("NodeRun", back_populates="assessor_result")

    def __repr__(self) -> str:
        return f"<AssessorResult id={self.id} parcel={self.parcel_number!r}>"


class TaxResult(Base):
    """Parsed tax data for a single node run."""

    __tablename__ = "tax_results"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    node_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    parcel_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tax_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_due: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    amount_paid: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    due_date: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    screenshot_s3_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationship
    node_run: Mapped["NodeRun"] = relationship("NodeRun", back_populates="tax_result")

    def __repr__(self) -> str:
        return f"<TaxResult id={self.id} parcel={self.parcel_number!r}>"
