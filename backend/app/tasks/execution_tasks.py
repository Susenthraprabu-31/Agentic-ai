"""Execution Celery tasks for asynchronous background workflow processing."""

import asyncio
import uuid
from typing import Any

import os
import socket
from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import WorkerAsyncSessionLocal
from app.idempotency.idempotency_manager import IdempotencyManager
from app.models.execution import WorkflowRun
from app.resilience.retry_manager import retry_manager
from app.resilience.retry_policy import DATABASE_TRANSIENT_POLICY
from app.services.worker_monitor import WorkerMonitorService
from app.tasks.celery_app import celery_app
from app.workflow_engine.executor import WorkflowExecutor

logger = get_logger(__name__)


@celery_app.task(name="execute_workflow_run", bind=True, max_retries=3, queue="executions")
def execute_workflow_run(
    self,
    workflow_run_id: str,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """
    Celery worker task that executes a WorkflowRun using the WorkflowExecutor engine.
    Manages its own isolated AsyncSession and event loop.
    Guarantees idempotency, fencing protection, worker heartbeats, and safe state transitions.
    """
    run_uuid = uuid.UUID(workflow_run_id)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    logger.info(
        "Celery task received for WorkflowRun %s (Task ID: %s, Worker: %s, Fencing: %s)",
        workflow_run_id,
        self.request.id,
        worker_id,
        fencing_token,
    )

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

            # 1. Check if run is already in terminal state
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
            if run.status in terminal_states:
                logger.info(
                    "WorkflowRun %s is already in terminal state %s. Skipping redundant execution.",
                    run.id,
                    run.status,
                )
                return {
                    "workflow_run_id": str(run.id),
                    "status": run.status,
                    "duration_ms": run.duration_ms,
                    "error_message": run.error_message,
                }

            # 2. Acquire task idempotency claim
            task_claim = await IdempotencyManager.process_task_claim(
                db=session,
                task_name="execute_workflow_run",
                workflow_run_id=run_uuid,
            )

            if task_claim.is_replay and task_claim.status.value == "completed":
                logger.info("Celery task %s replay detected for completed run %s", self.request.id, run.id)
                return task_claim.response_payload or {
                    "workflow_run_id": str(run.id),
                    "status": run.status,
                    "duration_ms": run.duration_ms,
                    "error_message": run.error_message,
                }

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
                try:
                    await monitor.record_heartbeat(
                        worker_id=worker_id,
                        active_task_count=0,
                        current_task_id=None,
                        status="ONLINE",
                    )
                except Exception as exc:
                    logger.debug("Failed to record worker stop heartbeat: %s", exc)

    async def _run() -> dict[str, Any]:
        return await retry_manager.execute(
            _execute_with_fresh_session,
            policy=DATABASE_TRANSIENT_POLICY,
            operation_name="celery_workflow_run",
            operation_id=workflow_run_id,
            workflow_run_id=run_uuid,
        )

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(_run())).result()
        else:
            return asyncio.run(_run())
    except Exception as exc:
        logger.exception("Execution error in Celery task for WorkflowRun %s: %s", workflow_run_id, exc)
        # Retry only transient infrastructure exceptions (e.g. database disconnect)
        if "OperationalError" in str(type(exc)) or "InterfaceError" in str(type(exc)):
            raise self.retry(exc=exc, countdown=5)
        return {
            "workflow_run_id": workflow_run_id,
            "status": "FAILED",
            "error_message": str(exc),
        }
