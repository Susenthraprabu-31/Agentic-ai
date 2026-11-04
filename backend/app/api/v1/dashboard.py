"""Dashboard router — summary metrics, KPI stats, and recent execution activity."""

from datetime import datetime, timezone, time
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.county_source import CountySource
from app.models.execution import WorkflowRun
from app.models.recipe import Recipe
from app.models.user import User
from app.models.workflow import Workflow

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class RecentActivityItem(BaseModel):
    id: str
    workflow_id: str
    workflow_name: str
    status: str
    duration_ms: int | None = None
    created_at: str


class DashboardSummaryResponse(BaseModel):
    total_workflows: int
    county_sources: int
    executions_today: int
    total_executions: int
    active_recipes: int
    recent_activity: list[RecentActivityItem]


@router.get("/summary", response_model=DashboardSummaryResponse, summary="Get dashboard summary and KPI stats")
async def get_dashboard_summary(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> DashboardSummaryResponse:
    """Retrieve high-level dashboard KPI metrics and recent execution history."""
    # 1. Total workflows
    wf_res = await db.execute(select(func.count(Workflow.id)))
    total_workflows = wf_res.scalar() or 0

    # 2. Total county sources
    cs_res = await db.execute(select(func.count(CountySource.id)))
    county_sources = cs_res.scalar() or 0

    # 3. Active recipes (not deprecated)
    rec_res = await db.execute(
        select(func.count(Recipe.id)).where(Recipe.status != "deprecated")
    )
    active_recipes = rec_res.scalar() or 0
    if active_recipes == 0:
        # Fallback to total recipes if none have specific lifecycle status
        rec_all = await db.execute(select(func.count(Recipe.id)))
        active_recipes = rec_all.scalar() or 0

    # 4. Executions today (created since UTC 00:00:00)
    now = datetime.now(timezone.utc)
    today_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    exec_today_res = await db.execute(
        select(func.count(WorkflowRun.id)).where(WorkflowRun.created_at >= today_start)
    )
    executions_today = exec_today_res.scalar() or 0

    # 5. Total executions
    exec_total_res = await db.execute(select(func.count(WorkflowRun.id)))
    total_executions = exec_total_res.scalar() or 0

    # 6. Recent activity (latest 5 executions)
    query = (
        select(WorkflowRun)
        .options(selectinload(WorkflowRun.workflow))
        .order_by(WorkflowRun.created_at.desc())
        .limit(5)
    )
    runs_res = await db.execute(query)
    runs = runs_res.scalars().all()

    recent_activity = [
        RecentActivityItem(
            id=str(r.id),
            workflow_id=str(r.workflow_id),
            workflow_name=r.workflow.name if r.workflow else "Workflow Execution",
            status=r.status,
            duration_ms=r.duration_ms,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in runs
    ]

    return DashboardSummaryResponse(
        total_workflows=total_workflows,
        county_sources=county_sources,
        executions_today=executions_today,
        total_executions=total_executions,
        active_recipes=active_recipes,
        recent_activity=recent_activity,
    )

