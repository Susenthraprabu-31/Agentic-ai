"""Health check schemas."""

from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Full health check response with dependency status."""

    status: str
    version: str
    environment: str
    database: str
    redis: str
    timestamp: datetime


class LivenessResponse(BaseModel):
    """Liveness probe — confirms the process is alive."""

    status: str
