"""Executions router — query WorkflowRun, NodeRun, results, and cancel background tasks."""

import io
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
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


@router.get(
    "/{run_id}/documents/download-all",
    summary="Download all execution documents as a ZIP archive",
)
async def download_all_run_documents(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> StreamingResponse:
    """Stream a ZIP archive containing every downloadable document artifact for this execution."""
    from app.documents.document_service import DocumentService

    doc_service = DocumentService(db=db)
    try:
        data, filename = await doc_service.build_execution_documents_zip(run_id, current_user)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(data)),
        },
    )


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
    run_row = await service.get_execution(
        run_id=run_id, user_id=current_user.id, is_superuser=current_user.is_superuser
    )

    from app.services.browser_session_service import BrowserSessionService
    sess_service = BrowserSessionService(db)
    sessions = await sess_service.get_sessions_for_run(run_id)

    activity_events = []
    latest_session = None
    if sessions:
        latest_session = sessions[0]
        for s in reversed(sessions):
            activity_events.extend(s.activity_log or [])

    browser_state = latest_session.state if latest_session else "IDLE"
    if run_row.status.upper() in ("CANCELLED", "CANCEL_REQUESTED"):
        browser_state = "CLOSED"

    from app.services.security_manual_intervention import (
        get_manual_intervention_pref,
        get_manual_intervention_state,
    )

    manual_state = await get_manual_intervention_state(str(run_id))
    manual_pref = await get_manual_intervention_pref(str(run_id))

    return {
        "workflow_run_id": str(run_id),
        "session_id": latest_session.session_id if latest_session else None,
        "browser_state": browser_state,
        "current_url": latest_session.current_url if latest_session else None,
        "current_node": latest_session.current_node if latest_session else None,
        "current_step": latest_session.current_step if latest_session else None,
        "current_action": latest_session.current_action if latest_session else None,
        "is_healthy": latest_session.is_healthy if latest_session else True,
        "activities": activity_events,
        "manual_security_intervention": manual_state,
        "manual_intervention_pref": manual_pref,
    }


@router.get(
    "/{run_id}/manual-intervention",
    summary="Get manual Cloudflare/CAPTCHA assistance preference for this run",
)
async def get_manual_intervention(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    service = ExecutionService(db)
    await service.get_execution(run_id=run_id, user_id=current_user.id, is_superuser=current_user.is_superuser)
    from app.services.security_manual_intervention import get_manual_intervention_pref

    return {"workflow_run_id": str(run_id), **await get_manual_intervention_pref(str(run_id))}


@router.put(
    "/{run_id}/manual-intervention",
    summary="Enable or disable manual Cloudflare/CAPTCHA assistance for this run",
)
async def put_manual_intervention(
    run_id: uuid.UUID,
    body: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    service = ExecutionService(db)
    await service.get_execution(run_id=run_id, user_id=current_user.id, is_superuser=current_user.is_superuser)
    if "enabled" not in body:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="enabled (boolean) required")
    from app.services.security_manual_intervention import (
        get_manual_intervention_pref,
        set_manual_intervention_pref,
    )

    await set_manual_intervention_pref(str(run_id), bool(body["enabled"]))
    pref = await get_manual_intervention_pref(str(run_id))
    return {"workflow_run_id": str(run_id), **pref}


@router.get(
    "/{run_id}/security-intervention",
    summary="Get active manual Cloudflare/CAPTCHA intervention state",
)
async def get_security_intervention(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    service = ExecutionService(db)
    await service.get_execution(run_id=run_id, user_id=current_user.id, is_superuser=current_user.is_superuser)
    from app.services.security_manual_intervention import get_manual_intervention_state

    state = await get_manual_intervention_state(str(run_id))
    return {"workflow_run_id": str(run_id), "manual_security_intervention": state}


@router.post(
    "/{run_id}/security-intervention/complete",
    summary="Signal that human completed Cloudflare/CAPTCHA verification",
)
async def complete_security_intervention(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    service = ExecutionService(db)
    await service.get_execution(run_id=run_id, user_id=current_user.id, is_superuser=current_user.is_superuser)
    from app.services.security_manual_intervention import signal_manual_intervention_complete

    ok = await signal_manual_intervention_complete(str(run_id))
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not record verification signal. Try again or check Redis.",
        )
    return {
        "workflow_run_id": str(run_id),
        "acknowledged": True,
        "message": "Verification noted. Automation will resume when the barrier clears.",
    }


@router.get(
    "/{run_id}/browser-live-view",
    summary="Get latest live Chrome viewport frame for workflow execution",
    responses={
        200: {"content": {"image/jpeg": {}}},
        404: {"description": "No live frame available for this run"},
    },
)
async def get_execution_browser_live_view(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> Response:
    """Return the most recent Playwright Chrome viewport JPEG captured during this run."""
    service = ExecutionService(db)
    await service.get_execution(run_id=run_id, user_id=current_user.id, is_superuser=current_user.is_superuser)

    from app.services.browser_live_view import get_latest_frame

    frame, meta = await get_latest_frame(str(run_id))
    if not frame:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No live Chrome browser frame is available for this execution yet.",
        )

    headers: dict[str, str] = {"Cache-Control": "no-store, no-cache, must-revalidate"}
    if meta.get("url"):
        headers["X-Frame-Url"] = str(meta["url"])[:2048]
    if meta.get("captured_at"):
        headers["X-Frame-Captured-At"] = str(meta["captured_at"])
    if meta.get("viewport_width"):
        headers["X-Viewport-Width"] = str(meta["viewport_width"])
    if meta.get("viewport_height"):
        headers["X-Viewport-Height"] = str(meta["viewport_height"])

    return Response(content=frame, media_type="image/jpeg", headers=headers)


@router.post(
    "/{run_id}/browser-live-view/pointer",
    summary="Send a click on the live Chrome viewport (relays to worker Playwright)",
)
async def post_browser_live_pointer(
    run_id: uuid.UUID,
    body: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    service = ExecutionService(db)
    await service.get_execution(run_id=run_id, user_id=current_user.id, is_superuser=current_user.is_superuser)
    for field in ("x", "y", "client_width", "client_height"):
        if field not in body:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{field} is required",
            )
    from app.services.browser_live_input import enqueue_pointer_event

    ok = await enqueue_pointer_event(
        str(run_id),
        action=str(body.get("action") or "click"),
        x=float(body["x"]),
        y=float(body["y"]),
        client_width=float(body["client_width"]),
        client_height=float(body["client_height"]),
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not queue pointer event (is Redis available and the worker running?)",
        )
    return {"workflow_run_id": str(run_id), "queued": True, "action": body.get("action") or "click"}


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
