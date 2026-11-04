"""AIUsageLog ORM model."""

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, Numeric, String, text
from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class AIProvider(str, enum.Enum):
    openai = "openai"
    mistral = "mistral"


class AIUsageLog(Base):
    """Tracks token usage and cost per AI API call."""

    __tablename__ = "ai_usage_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[AIProvider] = mapped_column(
        Enum(AIProvider, name="ai_provider"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    operation: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<AIUsageLog id={self.id} provider={self.provider} model={self.model!r}>"
