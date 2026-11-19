"""Fast cooperative cancellation signalling for workflow runs (Redis + DB)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.models.execution import NodeRun, RunStatus, WorkflowRun

logger = get_logger(__name__)


class ExecutionCancelledError(Exception):
    """Raised when automation must stop because the run was cancelled."""

_redis_client: Any | None = None
_redis_loop: Any | None = None

_CANCEL_TTL_SECONDS = 86_400


def _cancel_key(run_id: str) -> str:
    return f"execution:cancel:{run_id}"


async def _redis() -> Any | None:
    global _redis_client, _redis_loop
    try:
        import asyncio

        loop = asyncio.get_running_loop()
        if _redis_client is not None and _redis_loop == loop:
            return _redis_client
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await client.ping()
        _redis_client = client
        _redis_loop = loop
        return client
    except Exception as exc:
        logger.debug("Redis unavailable for execution cancel: %s", exc)
        _redis_client = None
        _redis_loop = None
        return None


async def signal_cancel(run_id: str | uuid.UUID) -> None:
    client = await _redis()
    if not client:
        return
    try:
        await client.set(_cancel_key(str(run_id)), "1", ex=_CANCEL_TTL_SECONDS)
    except Exception as exc:
        logger.warning("Failed to set execution cancel flag for %s: %s", run_id, exc)


async def is_cancel_signalled(run_id: str | uuid.UUID) -> bool:
    client = await _redis()
    if not client:
        return False
    try:
        val = await client.get(_cancel_key(str(run_id)))
        return val == "1"
    except Exception:
        return False


_TERMINAL_NODE = frozenset({"COMPLETED", "FAILED", "SKIPPED", "CANCELLED"})


async def finalize_run_cancelled(
    db: AsyncSession,
    run: WorkflowRun,
    *,
    reason: str = "Cancelled by user",
) -> WorkflowRun:
    """Mark the run and open node runs as CANCELLED immediately (bumps fencing token)."""
    now = datetime.now(timezone.utc)
    run.fencing_token = (run.fencing_token or 0) + 1
    run.status = RunStatus.CANCELLED.value
    run.completed_at = now
    if not run.error_message:
        run.error_message = reason
    if run.started_at and not run.duration_ms:
        run.duration_ms = max(1, int((now - run.started_at).total_seconds() * 1000))

    for nr in run.node_runs or []:
        if (nr.status or "").upper() not in _TERMINAL_NODE:
            nr.status = "CANCELLED"
            nr.error_message = nr.error_message or reason
            nr.completed_at = now
            if nr.started_at and not nr.duration_ms:
                nr.duration_ms = max(1, int((now - nr.started_at).total_seconds() * 1000))

    await db.flush()
    return run


async def revoke_celery_task(task_id: str | None, *, terminate: bool = True) -> None:
    if not task_id:
        return
    try:
        import sys

        from app.tasks.celery_app import celery_app

        kwargs: dict[str, Any] = {"terminate": terminate}
        if terminate and not sys.platform.startswith("win"):
            kwargs["signal"] = "SIGTERM"
        celery_app.control.revoke(task_id, **kwargs)
    except Exception as exc:
        logger.warning("Could not revoke Celery task %s: %s", task_id, exc)


async def abort_worker_automation(run_id: str | uuid.UUID) -> None:
    """Stop Playwright, live frames, and browser session activity in the Celery worker process."""
    rid = str(run_id)
    try:
        from app.services.browser_live_view import clear_live_frames, stop_live_capture_for_run

        await stop_live_capture_for_run(rid)
        await clear_live_frames(rid)
    except Exception as exc:
        logger.debug("Stop live capture on cancel: %s", exc)
    try:
        from app.browser.browser_manager import BrowserManager

        manager = await BrowserManager.get_instance()
        await manager.close()
    except Exception as exc:
        logger.debug("Browser close on cancel: %s", exc)


async def run_cancel_watchdog(run_id: uuid.UUID, stop: asyncio.Event) -> None:
    """Poll Redis until cancel is signalled, then tear down in-process Playwright automation."""
    while not stop.is_set():
        try:
            if await is_cancel_signalled(run_id):
                logger.info("Cancel watchdog stopping automation for run %s", run_id)
                await abort_worker_automation(run_id)
                return
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.debug("Cancel watchdog note: %s", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.35)
            return
        except asyncio.TimeoutError:
            continue


async def close_browser_sessions_for_run(db: AsyncSession, run_id: uuid.UUID) -> None:
    from app.models.browser_session import BrowserLifecycleState
    from app.services.browser_session_service import BrowserSessionService

    svc = BrowserSessionService(db)

    async def _close(session: AsyncSession) -> None:
        from sqlalchemy import select

        from app.models.browser_session import BrowserSessionRecord

        stmt = select(BrowserSessionRecord).where(BrowserSessionRecord.workflow_run_id == run_id)
        res = await session.execute(stmt)
        for rec in res.scalars().all():
            if rec.state not in (
                BrowserLifecycleState.CLOSED.value,
                BrowserLifecycleState.CRASHED.value,
            ):
                rec.state = BrowserLifecycleState.CLOSED.value
                rec.is_healthy = False
                rec.current_action = "CANCELLED"
                rec.current_step = "Run cancelled by user"
                rec.closed_at = datetime.now(timezone.utc)
        await session.flush()

    await svc._execute_with_session(_close)


async def load_run_for_cancel(db: AsyncSession, run_id: uuid.UUID) -> WorkflowRun | None:
    q = (
        select(WorkflowRun)
        .where(WorkflowRun.id == run_id)
        .options(selectinload(WorkflowRun.node_runs))
    )
    res = await db.execute(q)
    return res.scalar_one_or_none()