"""Workflows router — Visual Workflow Builder CRUD, versioning, and validation endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.idempotency.exceptions import (
    IdempotencyClaimLockedError,
    IdempotencyConflictError,
    IdempotencyKeyInvalidError,
)
from app.idempotency.idempotency_service import IdempotencyService
from app.models.user import User
from app.schemas.execution import WorkflowExecuteRequest, WorkflowRunResponse
from app.schemas.workflow import (
    WorkflowCreateRequest,
    WorkflowListResponse,
    WorkflowResponse,
    WorkflowUpdateRequest,
    WorkflowValidationRequest,
    WorkflowValidationResponse,
    WorkflowVersionCreateRequest,
    WorkflowVersionResponse,
)
from app.services.execution_service import ExecutionService
from app.services.workflow_service import WorkflowService

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("", response_model=WorkflowListResponse, summary="List workflows")
async def list_workflows(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
    search: str | None = Query(None, description="Search term for workflow name"),
    is_active: bool | None = Query(None, description="Filter by active status"),
    county_source_id: uuid.UUID | None = Query(None, description="Filter by county source"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> WorkflowListResponse:
    """List workflows accessible to the user."""
    service = WorkflowService(db)
    return await service.list_workflows(
        search=search,
        is_active=is_active,
        county_source_id=county_source_id,
        skip=skip,
        limit=limit,
    )


@router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED, summary="Create workflow")
async def create_workflow(
    payload: WorkflowCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> WorkflowResponse:
    """Create a new visual title search workflow with initial version."""
    service = WorkflowService(db)
    return await service.create_workflow(payload=payload, owner_id=current_user.id)


@router.post(
    "/validate",
    response_model=WorkflowValidationResponse,
    summary="Validate workflow definition graph",
)
async def validate_workflow(
    payload: WorkflowValidationRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> WorkflowValidationResponse:
    """Validate DAG acyclicity, node types, independent branches, and partial success settings."""
    service = WorkflowService(db)
    return service.validate_workflow_definition(payload.definition)


@router.post(
    "/{workflow_id}/execute",
    response_model=WorkflowRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger background workflow execution",
)
async def execute_workflow(
    workflow_id: uuid.UUID,
    payload: WorkflowExecuteRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
    response: Response,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> WorkflowRunResponse:
    """Submit workflow execution to background Celery queue. Returns HTTP 202 Accepted with execution ID."""
    exec_service = ExecutionService(db)

    # 1. Without Idempotency-Key, preserve standard execution flow
    if not idempotency_key:
        return await exec_service.submit_execution(
            workflow_id=workflow_id,
            user_id=current_user.id,
            payload=payload,
            is_superuser=current_user.is_superuser,
        )

    # 2. With Idempotency-Key, atomically claim or replay execution
    idempotency_service = IdempotencyService(db)
    try:
        claim = await idempotency_service.claim_operation(
            raw_key=idempotency_key,
            user_id=current_user.id,
            operation_type="workflow_execute",
            resource_id=str(workflow_id),
            payload=payload,
            ttl_seconds=86400,
        )
    except IdempotencyConflictError as conflict_err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=conflict_err.message,
        )
    except IdempotencyKeyInvalidError as invalid_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=invalid_err.message,
        )

    # Case A: Replay completed or existing execution
    if claim.is_replay and claim.workflow_run_id:
        response.status_code = status.HTTP_200_OK
        response.headers["X-Idempotent-Replay"] = "true"
        return await exec_service.get_execution(
            run_id=claim.workflow_run_id,
            user_id=current_user.id,
            is_superuser=current_user.is_superuser,
        )

    # Case B: New execution
    try:
        run_res = await exec_service.submit_execution(
            workflow_id=workflow_id,
            user_id=current_user.id,
            payload=payload,
            is_superuser=current_user.is_superuser,
        )
        await idempotency_service.complete_operation(
            claim_id=claim.id,
            workflow_run_id=run_res.id,
            response_status_code=202,
        )
        await db.commit()
        return run_res
    except Exception as exc:
        await idempotency_service.fail_operation(claim.id, error_message=str(exc))
        await db.commit()
        raise


@router.get("/{workflow_id}", response_model=WorkflowResponse, summary="Get workflow by ID")
async def get_workflow(
    workflow_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> WorkflowResponse:
    """Get a workflow by ID including its versions."""
    service = WorkflowService(db)
    return await service.get_workflow(workflow_id)


@router.put("/{workflow_id}", response_model=WorkflowResponse, summary="Update workflow metadata")
async def update_workflow(
    workflow_id: uuid.UUID,
    payload: WorkflowUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> WorkflowResponse:
    """Update workflow metadata (name, description, active status)."""
    service = WorkflowService(db)
    return await service.update_workflow(
        workflow_id=workflow_id,
        payload=payload,
        user_id=current_user.id,
        is_superuser=current_user.is_superuser,
    )


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete workflow")
async def delete_workflow(
    workflow_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> None:
    """Delete a workflow and all its versions."""
    service = WorkflowService(db)
    await service.delete_workflow(
        workflow_id=workflow_id,
        user_id=current_user.id,
        is_superuser=current_user.is_superuser,
    )


# ---------------------------------------------------------------------------
# Workflow Versions Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{workflow_id}/versions",
    response_model=list[WorkflowVersionResponse],
    summary="List workflow versions",
)
async def list_workflow_versions(
    workflow_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[WorkflowVersionResponse]:
    """Retrieve version history for a workflow."""
    service = WorkflowService(db)
    return await service.list_workflow_versions(workflow_id)


@router.post(
    "/{workflow_id}/versions",
    response_model=WorkflowVersionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create new workflow version",
)
async def create_workflow_version(
    workflow_id: uuid.UUID,
    payload: WorkflowVersionCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> WorkflowVersionResponse:
    """Save an immutable workflow snapshot version with validation."""
    service = WorkflowService(db)
    return await service.create_workflow_version(
        workflow_id=workflow_id,
        payload=payload,
        user_id=current_user.id,
        is_superuser=current_user.is_superuser,
    )


@router.get(
    "/{workflow_id}/versions/{version_id}",
    response_model=WorkflowVersionResponse,
    summary="Get specific workflow version",
)
async def get_workflow_version(
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> WorkflowVersionResponse:
    """Get specific version of a workflow."""
    service = WorkflowService(db)
    return await service.get_workflow_version(workflow_id, version_id)


@router.post(
    "/{workflow_id}/versions/{version_id}/set-current",
    response_model=WorkflowVersionResponse,
    summary="Set version as active current target",
)
async def set_current_version(
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> WorkflowVersionResponse:
    """Set the specified version as the active execution target for the workflow."""
    service = WorkflowService(db)
    return await service.set_current_version(
        workflow_id=workflow_id,
        version_id=version_id,
        user_id=current_user.id,
        is_superuser=current_user.is_superuser,
    )
