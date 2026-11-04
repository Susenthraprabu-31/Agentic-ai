"""Idempotency execution context and claim state structures."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.models.idempotency_record import IdempotencyStatus


@dataclass
class IdempotencyClaim:
    """Represents the outcome of attempting to acquire or replay an idempotent operation."""

    id: uuid.UUID
    is_replay: bool
    status: IdempotencyStatus
    workflow_run_id: uuid.UUID | None = None
    node_run_id: uuid.UUID | None = None
    response_status_code: int | None = None
    response_payload: dict[str, Any] | None = None
    expires_at: datetime | None = None
    operation_type: str = "workflow_execute"
    resource_id: str = ""

