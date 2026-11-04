"""SecurityEvent ORM model."""

import enum
import uuid

from sqlalchemy import DateTime, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class SecurityEventType(str, enum.Enum):
    cloudflare = "cloudflare"
    captcha = "captcha"
    turnstile = "turnstile"
    rate_limit = "rate_limit"
    blocked = "blocked"
    other = "other"


class SecurityEvent(Base):
    """Records a detected security/anti-bot event during a workflow run."""

    __tablename__ = "security_events"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    county_source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("county_sources.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default=SecurityEventType.other.value
    )
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    detected_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<SecurityEvent id={self.id} type={self.event_type}>"
