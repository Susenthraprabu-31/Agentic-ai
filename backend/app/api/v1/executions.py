"""Executions router — query WorkflowRun, NodeRun, results, and cancel background tasks."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.browser_session import BrowserSessionResponse
from app.schemas.document import DocumentArtifactResponse
from app.schemas.execution import (
    ExecutionResultsResponse,
    NodeRunResponse,
    WorkflowRunListResponse,
    WorkflowRunResponse,
)
from app.services.execution_service import ExecutionService

router = APIRouter(prefix="/executions", tags=["executions"])


@router.get("", response_model=WorkflowRunListResponse, summary="List workflow runs")
async def list_runs(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
    workflow_id: uuid.UUID | None = Query(None, description="Filter by workflow ID"),
    status: str | None = Query(None, description="Filter by execution status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> WorkflowRunListResponse:
    """List execution history for the authenticated user."""
    service = ExecutionService(db)
    return await service.list_executions(
        user_id=current_user.id,
        is_superuser=current_user.is_superuser,
        workflow_id=workflow_id,
        status_filter=status,
        skip=skip,
        limit=limit,
    )


@router.get("/{run_id}", response_model=WorkflowRunResponse, summary="Get workflow run details")
async def get_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> WorkflowRunResponse:
    """Retrieve details and node states for a specific workflow execution."""
    service = ExecutionService(db)
    return await service.get_execution(
        run_id=run_id,
        user_id=current_user.id,
        is_superuser=current_user.is_superuser,
    )


@router.get(
    "/{run_id}/results",
    response_model=ExecutionResultsResponse,
    summary="Get execution results and normalized property data",
)
async def get_run_results(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> ExecutionResultsResponse:
    """Retrieve aggregated assessor, tax, and normalized title search results."""
    service = ExecutionService(db)
    return await service.get_execution_results(
        run_id=run_id,
        user_id=current_user.id,
        is_superuser=current_user.is_superuser,
    )


@router.get(
    "/{run_id}/nodes",
    response_model=list[NodeRunResponse],
    summary="List node runs for an execution",
)
async def list_node_runs(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[NodeRunResponse]:
    """Retrieve detailed node execution timeline and step results."""
    service = ExecutionService(db)
    return await service.list_node_runs(
        run_id=run_id,
        user_id=current_user.id,
        is_superuser=current_user.is_superuser,
    )


@router.get(
    "/{run_id}/documents",
    response_model=list[DocumentArtifactResponse],
    summary="Get documents retrieved during execution",
)
async def get_run_documents(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[DocumentArtifactResponse]:
    """Retrieve all document artifacts retrieved for a specific workflow execution."""
    service = ExecutionService(db)
    docs = await service.get_execution_documents(
        run_id=run_id,
        user_id=current_user.id,
        is_superuser=current_user.is_superuser,
    )
    return [DocumentArtifactResponse.model_validate(doc) for doc in docs]


@router.post(
    "/{run_id}/cancel",
    response_model=WorkflowRunResponse,
    summary="Cancel workflow run",
)
async def cancel_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> WorkflowRunResponse:
    """Request non-destructive cancellation of an ongoing background workflow execution."""
    service = ExecutionService(db)
    return await service.cancel_execution(
        run_id=run_id,
        user_id=current_user.id,
        is_superuser=current_user.is_superuser,
    )


@router.get(
    "/{run_id}/recovery",
    summary="Get workflow execution recovery status and history",
)
async def get_execution_recovery(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """Retrieve recovery attempt history, worker ownership, and fencing token status."""
    service = ExecutionService(db)
    # Check access permission
    await service.get_execution(run_id=run_id, user_id=current_user.id, is_superuser=current_user.is_superuser)

    from app.services.recovery_service import RecoveryService
    rec_service = RecoveryService(db)
    return await rec_service.get_recovery_history(run_id=run_id)


@router.post(
    "/{run_id}/recover",
    summary="Manually trigger workflow execution recovery",
)
async def manual_recover_execution(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """Authorized manual recovery trigger for an interrupted or orphaned execution."""
    service = ExecutionService(db)
    # Check access permission
    await service.get_execution(run_id=run_id, user_id=current_user.id, is_superuser=current_user.is_superuser)

    from app.services.recovery_service import RecoveryService
    rec_service = RecoveryService(db)
    success, message = await rec_service.claim_and_recover_run(
        run_id=run_id,
        reason=f"manual_recovery_by_user_{current_user.email}",
        manual=True,
    )
    if not success:
        from fastapi import HTTPException
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)

    return {"success": True, "message": message, "workflow_run_id": str(run_id)}


@router.get(
    "/{run_id}/browser-sessions",
    response_model=list[BrowserSessionResponse],
    summary="Get browser sessions for workflow execution",
)
async def get_execution_browser_sessions(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[BrowserSessionResponse]:
    """Retrieve all Playwright browser session states, health, and page metrics for this execution."""
    service = ExecutionService(db)
    await service.get_execution(run_id=run_id, user_id=current_user.id, is_superuser=current_user.is_superuser)

    from app.services.browser_session_service import BrowserSessionService
    sess_service = BrowserSessionService(db)
    sessions = await sess_service.get_sessions_for_run(run_id)
    return [BrowserSessionResponse.model_validate(s) for s in sessions]


@router.get(
    "/{run_id}/browser-activity",
    summary="Get live browser activity and step log for workflow execution",
)
async def get_execution_browser_activity(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """Retrieve aggregated live browser session activity events, active URL, and step status."""
    service = ExecutionService(db)
    await service.get_execution(run_id=run_id, user_id=current_user.id, is_superuser=current_user.is_superuser)

    from app.services.browser_session_service import BrowserSessionService
    sess_service = BrowserSessionService(db)
    sessions = await sess_service.get_sessions_for_run(run_id)

    activity_events = []
    latest_session = None
    if sessions:
        latest_session = sessions[0]
        for s in reversed(sessions):
            activity_events.extend(s.activity_log or [])

    return {
        "workflow_run_id": str(run_id),
        "session_id": latest_session.session_id if latest_session else None,
        "browser_state": latest_session.state if latest_session else "IDLE",
        "current_url": latest_session.current_url if latest_session else None,
        "current_node": latest_session.current_node if latest_session else None,
        "current_step": latest_session.current_step if latest_session else None,
        "current_action": latest_session.current_action if latest_session else None,
        "is_healthy": latest_session.is_healthy if latest_session else True,
        "activities": activity_events,
    }


@router.get(
    "/{run_id}/download-status",
    summary="Get aggregated document download status for workflow execution",
)
async def get_execution_download_status(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """Retrieve summarized document download progress, byte counts, and attempt statuses for this execution."""
    service = ExecutionService(db)
    await service.get_execution(run_id=run_id, user_id=current_user.id, is_superuser=current_user.is_superuser)

    from app.documents.document_service import DocumentService
    doc_service = DocumentService(db=db)
    return await doc_service.get_download_status_for_execution(run_id)
