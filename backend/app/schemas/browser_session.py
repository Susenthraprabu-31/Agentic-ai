"""Pydantic schemas for BrowserSessionRecord entities."""

from datetime import datetime
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict, Field


class BrowserSessionResponse(BaseModel):
    """Schema for browser session state and metrics."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: str
    workflow_run_id: uuid.UUID | None = None
    worker_id: str | None = None
    browser_type: str
    state: str
    is_healthy: bool
    pages_created: int
    contexts_created: int
    failure_count: int
    last_failure_reason: str | None = None
    current_url: str | None = None
    current_node: str | None = None
    current_step: str | None = None
    current_action: str | None = None
    activity_log: list[dict[str, Any]] = Field(default_factory=list)
    started_at: datetime
    last_activity_at: datetime
    closed_at: datetime | None = None

