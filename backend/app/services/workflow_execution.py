"""Shared workflow run execution for Celery workers and the desktop job runner."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import WorkerAsyncSessionLocal
from app.idempotency.idempotency_manager import IdempotencyManager
from app.models.execution import WorkflowRun
from app.resilience.retry_manager import retry_manager
from app.resilience.retry_policy import DATABASE_TRANSIENT_POLICY
from app.services.worker_monitor import WorkerMonitorService
from app.workflow_engine.executor import WorkflowExecutor

logger = get_logger(__name__)


async def _execute_workflow_run_async(
    workflow_run_id: str,
    *,
    fencing_token: int | None = None,
    worker_id: str,
    task_label: str = "execute_workflow_run",
) -> dict[str, Any]:
    run_uuid = uuid.UUID(workflow_run_id)

    async def _execute_with_fresh_session() -> dict[str, Any]:
        async with WorkerAsyncSessionLocal() as session:
            monitor = WorkerMonitorService(session)
            try:
                await monitor.record_heartbeat(
                    worker_id=worker_id,
                    active_task_count=1,
                    current_task_id=workflow_run_id,
                    status="BUSY",
                )
            except Exception as exc:
                logger.debug("Failed to record worker start heartbeat: %s", exc)

            query = select(WorkflowRun).where(WorkflowRun.id == run_uuid)
            res = await session.execute(query)
            run = res.scalar_one_or_none()
            if not run:
                return {
                    "workflow_run_id": workflow_run_id,
                    "status": "NOT_FOUND",
                    "error_message": f"WorkflowRun {workflow_run_id} not found.",
                }

            terminal_states = {"COMPLETED", "FAILED", "PARTIALLY_COMPLETED", "CANCELLED"}
            if run.status == "CANCELLED":
                return {
                    "workflow_run_id": str(run.id),
                    "status": run.status,
                    "duration_ms": run.duration_ms,
                    "error_message": run.error_message,
                }
            if run.status in terminal_states:
                return {
                    "workflow_run_id": str(run.id),
                    "status": run.status,
                    "duration_ms": run.duration_ms,
                    "error_message": run.error_message,
                }

            task_claim = await IdempotencyManager.process_task_claim(
                db=session,
                task_name=task_label,
                workflow_run_id=run_uuid,
            )

            if task_claim.is_replay and task_claim.status.value == "completed":
                return task_claim.response_payload or {
                    "workflow_run_id": str(run.id),
                    "status": run.status,
                    "duration_ms": run.duration_ms,
                    "error_message": run.error_message,
                }

            stop_watchdog = asyncio.Event()
            from app.services.execution_cancel import run_cancel_watchdog

            watchdog = asyncio.create_task(run_cancel_watchdog(run_uuid, stop_watchdog))
            try:
                executor = WorkflowExecutor(session)
                run = await executor.execute_run(
                    run_uuid,
                    expected_fencing_token=fencing_token,
                    worker_id=worker_id,
                )
                result_payload = {
                    "workflow_run_id": str(run.id),
                    "status": run.status,
                    "duration_ms": run.duration_ms,
                    "error_message": run.error_message,
                }
                await IdempotencyManager.complete_claim(
                    db=session,
                    claim_id=task_claim.id,
                    workflow_run_id=run.id,
                    response_status_code=200,
                    response_payload=result_payload,
                )
                await session.commit()
                return result_payload
            except Exception as exc:
                await IdempotencyManager.fail_claim(
                    db=session,
                    claim_id=task_claim.id,
                    error_message=str(exc),
                )
                await session.commit()
                raise
            finally:
                stop_watchdog.set()
                watchdog.cancel()
                try:
                    await watchdog
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
                try:
                    await monitor.record_heartbeat(
                        worker_id=worker_id,
                        active_task_count=0,
                        current_task_id=None,
                        status="ONLINE",
                    )
                except Exception as exc:
                    logger.debug("Failed to record worker stop heartbeat: %s", exc)

    return await retry_manager.execute(
        _execute_with_fresh_session,
        policy=DATABASE_TRANSIENT_POLICY,
        operation_name=task_label,
        operation_id=workflow_run_id,
        workflow_run_id=run_uuid,
    )


async def run_workflow_in_process(
    workflow_run_id: str,
    *,
    fencing_token: int | None = None,
    worker_id: str,
) -> dict[str, Any]:
    """Execute a workflow run in the current asyncio event loop (desktop)."""
    return await _execute_workflow_run_async(
        workflow_run_id,
        fencing_token=fencing_token,
        worker_id=worker_id,
        task_label="local_execute_workflow_run",
    )


def run_workflow_sync_for_celery(
    workflow_run_id: str,
    *,
    fencing_token: int | None = None,
    worker_id: str,
    celery_task_id: str | None = None,
) -> dict[str, Any]:
    """Bridge Celery's synchronous worker thread to the async execution core."""

    async def _run() -> dict[str, Any]:
        return await _execute_workflow_run_async(
            workflow_run_id,
            fencing_token=fencing_token,
            worker_id=worker_id,
            task_label="execute_workflow_run",
        )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(_run())).result()
    return asyncio.run(_run())