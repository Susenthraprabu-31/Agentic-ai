"""Claim workflow runs from the database for local execution."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import RunStatus, WorkflowRun


async def claim_next_queued_run(session: AsyncSession) -> WorkflowRun | None:
    """Atomically claim one QUEUED run and mark it RUNNING."""
    stmt = (
        select(WorkflowRun)
        .where(WorkflowRun.status == RunStatus.QUEUED.value)
        .order_by(WorkflowRun.created_at.asc())
        .limit(1)
    )
    res = await session.execute(stmt)
    run = res.scalar_one_or_none()
    if not run:
        return None

    now = datetime.now(timezone.utc)
    fence = (run.fencing_token or 0) + 1
    upd = (
        update(WorkflowRun)
        .where(
            WorkflowRun.id == run.id,
            WorkflowRun.status == RunStatus.QUEUED.value,
            WorkflowRun.worker_id.is_(None),
        )
        .values(
            worker_id="local-job-runner",
            last_heartbeat_at=now,
            fencing_token=fence,
        )
    )
    result = await session.execute(upd)
    if result.rowcount != 1:
        await session.rollback()
        return None
    await session.commit()
    await session.refresh(run)
    return run


async def requeue_run_id(session: AsyncSession, run_id: uuid.UUID) -> None:
    await session.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == run_id)
        .values(status=RunStatus.QUEUED.value, worker_id=None)
    )
    await session.commit()