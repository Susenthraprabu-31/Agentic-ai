"""WorkerRecord ORM model representing active/registered Celery workers."""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class WorkerStatus(str, enum.Enum):
    ONLINE = "ONLINE"
    BUSY = "BUSY"
    UNRESPONSIVE = "UNRESPONSIVE"
    OFFLINE = "OFFLINE"
    RECOVERING = "RECOVERING"

    # Lowercase aliases
    online = "ONLINE"
    busy = "BUSY"
    unresponsive = "UNRESPONSIVE"
    offline = "OFFLINE"
    recovering = "RECOVERING"


class WorkerRecord(Base):
    """Tracks registered Celery background workers, heartbeats, and status."""

    __tablename__ = "worker_records"

    worker_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    queues: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=WorkerStatus.ONLINE.value, index=True
    )
    active_task_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    def __repr__(self) -> str:
        return f"<WorkerRecord id={self.worker_id!r} status={self.status} pid={self.pid}>"

