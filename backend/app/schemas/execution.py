"""Execution (WorkflowRun / NodeRun) request/response schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowExecuteRequest(BaseModel):
    workflow_version_id: uuid.UUID | None = None
    input_data: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunCreateRequest(BaseModel):
    workflow_id: uuid.UUID
    workflow_version_id: uuid.UUID | None = None
    input_data: dict[str, Any] = Field(default_factory=dict)


class NodeRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_run_id: uuid.UUID
    node_id: str
    node_type: str
    status: str
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_data: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    retry_count: int = 0
    county_source_id: uuid.UUID | None = None
    recipe_version_id: uuid.UUID | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    created_at: datetime


class WorkflowRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    workflow_version_id: uuid.UUID
    workflow_name: str | None = None
    version_number: int | None = None
    triggered_by: uuid.UUID
    celery_task_id: str | None = None
    status: str
    input_data: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    error_message: str | None = None
    execution_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    node_runs: list[NodeRunResponse] = Field(default_factory=list)


class WorkflowRunListResponse(BaseModel):
    items: list[WorkflowRunResponse]
    total: int


class ExecutionResultsResponse(BaseModel):
    workflow_run_id: uuid.UUID
    workflow_id: uuid.UUID
    workflow_version_id: uuid.UUID
    status: str
    duration_ms: int | None = None
    is_partial_success: bool = False
    assessor_branch_status: str = "NOT_CONFIGURED"
    tax_branch_status: str = "NOT_CONFIGURED"
    has_assessor_branch: bool = False
    has_tax_branch: bool = False
    has_normalize_branch: bool = False
    assessor_records_count: int = 0
    tax_records_count: int = 0
    assessor_result: dict[str, Any] | None = None
    tax_result: dict[str, Any] | None = None
    normalized_result: dict[str, Any] | None = None
    final_output: dict[str, Any] | None = None
    errors: list[str] = Field(default_factory=list)
