"""Workers router — query worker status, heartbeat health, and cluster capacity."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.user import User
from app.services.worker_monitor import WorkerMonitorService

router = APIRouter(prefix="/workers", tags=["workers"])


@router.get("", summary="List registered background workers")
async def list_workers(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[dict[str, Any]]:
    """List all registered Celery workers with status, heartbeat age, and active tasks."""
    monitor = WorkerMonitorService(db)
    return await monitor.get_workers()


@router.get("/health", summary="Get aggregate worker cluster health")
async def get_cluster_health(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict[str, Any]:
    """Retrieve overall worker cluster health, online counts, and unresponsive worker totals."""
    monitor = WorkerMonitorService(db)
    return await monitor.get_worker_health()


@router.get("/{worker_id}", summary="Get worker details by ID")
async def get_worker_detail(
    worker_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict[str, Any]:
    """Retrieve detailed state and metadata for a specific background worker."""
    monitor = WorkerMonitorService(db)
    worker = await monitor.get_worker(worker_id)
    if not worker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Worker '{worker_id}' not found.",
        )
    return worker

