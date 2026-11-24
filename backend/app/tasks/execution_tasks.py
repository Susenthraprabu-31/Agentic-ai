"""Execution Celery tasks for asynchronous background workflow processing."""

import os
import socket
import uuid
from typing import Any

from app.core.logging import get_logger
from app.services.workflow_execution import run_workflow_sync_for_celery
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="execute_workflow_run", bind=True, max_retries=3, queue="executions")
def execute_workflow_run(
    self,
    workflow_run_id: str,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """
    Celery worker task that executes a WorkflowRun using the WorkflowExecutor engine.
    """
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    logger.info(
        "Celery task received for WorkflowRun %s (Task ID: %s, Worker: %s, Fencing: %s)",
        workflow_run_id,
        self.request.id,
        worker_id,
        fencing_token,
    )
    try:
        return run_workflow_sync_for_celery(
            workflow_run_id,
            fencing_token=fencing_token,
            worker_id=worker_id,
            celery_task_id=str(self.request.id),
        )
    except Exception as exc:
        logger.exception("Execution error in Celery task for WorkflowRun %s: %s", workflow_run_id, exc)
        if "OperationalError" in str(type(exc)) or "InterfaceError" in str(type(exc)):
            raise self.retry(exc=exc, countdown=5)
        return {
            "workflow_run_id": workflow_run_id,
            "status": "FAILED",
            "error_message": str(exc),
        }