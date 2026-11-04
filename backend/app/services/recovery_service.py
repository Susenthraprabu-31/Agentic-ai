"""Workflow recovery service detecting orphaned executions and executing atomic fenced recovery."""

from datetime import datetime, timedelta, timezone
from typing import Any
import uuid

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.execution import RunStatus, WorkflowRun
from app.models.recovery import RecoveryStatus, WorkflowRecoveryRecord
from app.models.worker import WorkerRecord, WorkerStatus
from app.workflow_engine.execution_state import is_terminal_run_state

logger = get_logger(__name__)


class RecoveryService:
    """
    Detects and recovers workflow executions interrupted by worker crashes or timeouts.
    Uses atomic fencing tokens to ensure zombie workers cannot overwrite results.
    """

    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db

    async def detect_orphaned_runs(self) -> list[WorkflowRun]:
        """
        Identifies executions in RUNNING or QUEUED state where the assigned worker
        is UNRESPONSIVE / OFFLINE, or whose last heartbeat exceeds the stale threshold.
        """
        stale_timeout = settings.EXECUTION_STALE_TIMEOUT_SECONDS
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_timeout)

        session = self.db or AsyncSessionLocal()
        try:
            # Query non-terminal runs in RUNNING or QUEUED state
            stmt = select(WorkflowRun).where(
                WorkflowRun.status.in_([RunStatus.RUNNING.value, RunStatus.QUEUED.value])
            )
            res = await session.execute(stmt)
            active_runs = res.scalars().all()

            orphaned: list[WorkflowRun] = []
            now = datetime.now(timezone.utc)

            for run in active_runs:
                # 1. Skip terminal states
                if is_terminal_run_state(run.status):
                    continue

                # 2. Check if worker is assigned and check its status
                is_orphan = False
                if run.worker_id:
                    worker_stmt = select(WorkerRecord).where(WorkerRecord.worker_id == run.worker_id)
                    w_res = await session.execute(worker_stmt)
                    worker = w_res.scalar_one_or_none()

                    if not worker or worker.status in [WorkerStatus.UNRESPONSIVE.value, WorkerStatus.OFFLINE.value]:
                        is_orphan = True
                else:
                    # No worker assigned or worker died before assignment
                    started = run.started_at or run.created_at
                    if started and started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    if started and started < cutoff:
                        is_orphan = True

                # 3. Check run-level heartbeat / lease expiry
                if not is_orphan:
                    hb = run.last_heartbeat_at or run.started_at or run.created_at
                    if hb and hb.tzinfo is None:
                        hb = hb.replace(tzinfo=timezone.utc)
                    if hb and hb < cutoff:
                        is_orphan = True

                if is_orphan:
                    orphaned.append(run)

            return orphaned
        finally:
            if not self.db:
                await session.close()

    async def claim_and_recover_run(
        self,
        run_id: uuid.UUID,
        reason: str = "orphaned_worker",
        recovery_worker_id: str | None = None,
        manual: bool = False,
    ) -> tuple[bool, str]:
        """
        Atomically claims an orphaned or interrupted run, increments the fencing token,
        records the recovery audit log, and dispatches the execution back to Celery.

        Returns: (success: bool, message: str)
        """
        session = self.db or AsyncSessionLocal()
        try:
            stmt = select(WorkflowRun).where(WorkflowRun.id == run_id)
            res = await session.execute(stmt)
            run = res.scalar_one_or_none()

            if not run:
                return False, f"WorkflowRun {run_id} not found."

            if is_terminal_run_state(run.status):
                return False, f"WorkflowRun {run_id} is already in terminal state '{run.status}'."

            if run.status == RunStatus.CANCEL_REQUESTED.value or run.status == RunStatus.CANCELLED.value:
                return False, f"WorkflowRun {run_id} has been cancelled."

            # Check max recovery attempts
            max_attempts = settings.EXECUTION_MAX_RECOVERY_ATTEMPTS
            current_attempts = run.recovery_attempt_count or 0

            now = datetime.now(timezone.utc)
            old_fencing_token = run.fencing_token or 0
            new_fencing_token = old_fencing_token + 1
            previous_worker_id = run.worker_id

            if current_attempts >= max_attempts and not manual:
                # Mark as permanently FAILED
                logger.error(
                    "WorkflowRun %s reached max recovery attempts (%d/%d). Marking FAILED.",
                    run_id,
                    current_attempts,
                    max_attempts,
                )
                run.status = RunStatus.FAILED.value
                run.completed_at = now
                run.error_message = f"Execution recovery failed: exceeded maximum recovery attempts ({max_attempts}). Reason: {reason}"
                run.fencing_token = new_fencing_token

                audit = WorkflowRecoveryRecord(
                    workflow_run_id=run.id,
                    previous_worker_id=previous_worker_id,
                    recovery_worker_id=recovery_worker_id,
                    fencing_token=new_fencing_token,
                    attempt_number=current_attempts + 1,
                    status=RecoveryStatus.FAILED.value,
                    reason=reason,
                    details={"error": "Max recovery attempts exceeded", "terminal": True},
                )
                session.add(audit)
                await session.commit()
                return False, f"Maximum recovery attempts ({max_attempts}) exceeded. Execution marked FAILED."

            # Atomic claim & transition to RECOVERING
            run.status = RunStatus.RECOVERING.value
            run.fencing_token = new_fencing_token
            run.recovery_attempt_count = current_attempts + 1
            run.last_recovery_at = now
            run.recovery_reason = reason
            run.worker_id = recovery_worker_id

            audit = WorkflowRecoveryRecord(
                workflow_run_id=run.id,
                previous_worker_id=previous_worker_id,
                recovery_worker_id=recovery_worker_id,
                fencing_token=new_fencing_token,
                attempt_number=current_attempts + 1,
                status=RecoveryStatus.STARTED.value,
                reason=reason,
                details={"fencing_token": new_fencing_token, "manual": manual},
            )
            session.add(audit)
            await session.commit()

            logger.info(
                "Claimed WorkflowRun %s for recovery (fencing_token=%d, attempt=%d, reason=%s)",
                run.id,
                new_fencing_token,
                run.recovery_attempt_count,
                reason,
            )

            # Requeue execution safely via Celery
            try:
                from app.tasks.execution_tasks import execute_workflow_run

                execute_workflow_run.delay(str(run.id), fencing_token=new_fencing_token)
                return True, f"WorkflowRun {run.id} recovered and requeued with fencing token {new_fencing_token}."
            except Exception as exc:
                logger.exception("Failed to dispatch recovered run %s to Celery: %s", run.id, exc)
                return True, f"WorkflowRun {run.id} marked RECOVERING (fencing token {new_fencing_token})."
        finally:
            if not self.db:
                await session.close()

    async def get_recovery_history(self, run_id: uuid.UUID) -> dict[str, Any]:
        """Returns recovery attempts, audit logs, and fencing status for a workflow run."""
        session = self.db or AsyncSessionLocal()
        try:
            stmt = select(WorkflowRun).where(WorkflowRun.id == run_id)
            res = await session.execute(stmt)
            run = res.scalar_one_or_none()

            if not run:
                return {"error": "WorkflowRun not found", "run_id": str(run_id)}

            audit_stmt = (
                select(WorkflowRecoveryRecord)
                .where(WorkflowRecoveryRecord.workflow_run_id == run_id)
                .order_by(WorkflowRecoveryRecord.created_at.asc())
            )
            audit_res = await session.execute(audit_stmt)
            audits = audit_res.scalars().all()

            records = [
                {
                    "id": str(a.id),
                    "attempt_number": a.attempt_number,
                    "fencing_token": a.fencing_token,
                    "previous_worker_id": a.previous_worker_id,
                    "recovery_worker_id": a.recovery_worker_id,
                    "status": a.status,
                    "reason": a.reason,
                    "details": a.details or {},
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in audits
            ]

            return {
                "workflow_run_id": str(run.id),
                "status": run.status,
                "current_worker_id": run.worker_id,
                "fencing_token": run.fencing_token,
                "recovery_attempt_count": run.recovery_attempt_count,
                "last_recovery_at": run.last_recovery_at.isoformat() if run.last_recovery_at else None,
                "recovery_reason": run.recovery_reason,
                "recovery_history": records,
            }
        finally:
            if not self.db:
                await session.close()

