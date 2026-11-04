"""Workflow request/response schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    county_source_id: uuid.UUID | None = None
    initial_definition: dict[str, Any] | None = None


class WorkflowUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    is_active: bool | None = None
    county_source_id: uuid.UUID | None = None


class WorkflowVersionCreateRequest(BaseModel):
    definition: dict[str, Any]
    changelog: str | None = None
    is_current: bool = False


class WorkflowValidationRequest(BaseModel):
    definition: dict[str, Any]


class WorkflowValidationResponse(BaseModel):
    is_valid: bool
    errors: list[str]
    warnings: list[str]
    execution_order: list[str]
    node_count: int
    edge_count: int
    supports_assessor: bool
    supports_tax: bool
    supports_partial_success: bool


class WorkflowVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    version_number: int
    definition: dict[str, Any]
    changelog: str | None = None
    is_current: bool
    created_at: datetime


class WorkflowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    owner_id: uuid.UUID
    is_active: bool
    county_source_id: uuid.UUID | None = None
    current_version_id: uuid.UUID | None = None
    current_version_number: int | None = None
    created_at: datetime
    updated_at: datetime | None
    versions: list[WorkflowVersionResponse] = []


class WorkflowListResponse(BaseModel):
    items: list[WorkflowResponse]
    total: int
