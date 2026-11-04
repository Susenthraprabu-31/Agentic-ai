"""Browser session tracking and resilience service."""

from datetime import datetime, timezone
import os
import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.browser_session import BrowserLifecycleState, BrowserSessionRecord

logger = get_logger(__name__)


class BrowserSessionService:
    """Manages persistence and queries for Playwright browser sessions."""

    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db

    async def _execute_with_session(self, func_to_run):
        if self.db:
            return await func_to_run(self.db)
        import sys
        from app.db.session import AsyncSessionLocal, WorkerAsyncSessionLocal
        factory = (
            WorkerAsyncSessionLocal
            if (os.environ.get("CELERY_WORKER_ID") or any("celery" in arg.lower() for arg in sys.argv))
            else AsyncSessionLocal
        )
        async with factory() as session:
            try:
                res = await func_to_run(session)
                await session.commit()
                return res
            except Exception:
                await session.rollback()
                raise

    async def register_session(
        self,
        session_id: str,
        workflow_run_id: uuid.UUID | None = None,
        worker_id: str | None = None,
        browser_type: str = "chromium",
        state: str = BrowserLifecycleState.READY.value,
    ) -> BrowserSessionRecord:
        """Creates or registers a browser session record."""
        effective_worker_id = worker_id or os.environ.get("CELERY_WORKER_ID") or f"worker-{os.getpid()}"

        async def _do(session: AsyncSession):
            stmt = select(BrowserSessionRecord).where(BrowserSessionRecord.session_id == session_id)
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()

            now = datetime.now(timezone.utc)
            if record:
                record.state = state
                record.is_healthy = state not in (BrowserLifecycleState.CRASHED.value, BrowserLifecycleState.DISCONNECTED.value)
                record.last_activity_at = now
                if workflow_run_id:
                    record.workflow_run_id = workflow_run_id
                if worker_id:
                    record.worker_id = effective_worker_id
            else:
                record = BrowserSessionRecord(
                    session_id=session_id,
                    workflow_run_id=workflow_run_id,
                    worker_id=effective_worker_id,
                    browser_type=browser_type,
                    state=state,
                    is_healthy=True,
                    pages_created=0,
                    contexts_created=0,
                    failure_count=0,
                    started_at=now,
                    last_activity_at=now,
                )
                session.add(record)
            await session.flush()
            return record

        try:
            return await self._execute_with_session(_do)
        except Exception as e:
            logger.warning("Failed to register browser session %s in db: %s", session_id, e)
            return None

    async def update_state(
        self,
        session_id: str,
        state: str,
        is_healthy: bool | None = None,
        failure_reason: str | None = None,
        pages_delta: int = 0,
        contexts_delta: int = 0,
    ) -> None:
        """Updates the status and activity timestamps of a browser session."""
        async def _do(session: AsyncSession):
            stmt = select(BrowserSessionRecord).where(BrowserSessionRecord.session_id == session_id)
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if not record:
                return

            now = datetime.now(timezone.utc)
            record.state = state
            record.last_activity_at = now

            if is_healthy is not None:
                record.is_healthy = is_healthy
            else:
                record.is_healthy = state not in (
                    BrowserLifecycleState.CRASHED.value,
                    BrowserLifecycleState.DISCONNECTED.value,
                    BrowserLifecycleState.CLOSED.value,
                )

            if failure_reason:
                record.failure_count += 1
                record.last_failure_reason = failure_reason

            if pages_delta:
                record.pages_created += pages_delta
            if contexts_delta:
                record.contexts_created += contexts_delta

            if state == BrowserLifecycleState.CLOSED.value:
                record.closed_at = now

            await session.flush()

        try:
            await self._execute_with_session(_do)
        except Exception as e:
            logger.warning("Failed to update browser session %s in db: %s", session_id, e)

    async def record_activity(
        self,
        session_id: str,
        node_id: str | None = None,
        step_index: int | None = None,
        action: str | None = None,
        description: str | None = None,
        status: str = "RUNNING",
        url: str | None = None,
        error: str | None = None,
        duration_ms: int = 0,
    ) -> None:
        """Records an execution step or milestone event in the browser session's activity log."""
        async def _do(session: AsyncSession):
            stmt = select(BrowserSessionRecord).where(BrowserSessionRecord.session_id == session_id)
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if not record:
                return

            now = datetime.now(timezone.utc)
            record.last_activity_at = now
            if url:
                record.current_url = url
            if node_id:
                record.current_node = node_id
            if action:
                record.current_action = action
            if description:
                record.current_step = description
            elif step_index is not None:
                record.current_step = f"Step {step_index + 1}: {action}"

            log = list(record.activity_log or [])
            # If updating an in-progress step with matching step_index and node_id
            updated = False
            if step_index is not None and node_id:
                for entry in reversed(log):
                    if entry.get("node_id") == node_id and entry.get("step_index") == step_index and entry.get("status") == "RUNNING":
                        entry["status"] = status
                        entry["duration_ms"] = duration_ms
                        if error:
                            entry["error"] = error
                        if url:
                            entry["url"] = url
                        entry["completed_at"] = now.isoformat()
                        updated = True
                        break

            if not updated:
                entry = {
                    "id": str(uuid.uuid4())[:8],
                    "timestamp": now.isoformat(),
                    "node_id": node_id,
                    "step_index": step_index,
                    "action": action,
                    "description": description or action,
                    "status": status,
                    "url": url,
                    "error": error,
                    "duration_ms": duration_ms,
                }
                log.append(entry)

            record.activity_log = log
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(record, "activity_log")
            await session.flush()

        try:
            await self._execute_with_session(_do)
        except Exception as e:
            logger.warning("Failed to record browser activity for session %s: %s", session_id, e)

    async def get_sessions_for_run(self, workflow_run_id: uuid.UUID) -> list[BrowserSessionRecord]:
        """Fetches all browser sessions associated with a given workflow run."""
        async def _do(session: AsyncSession):
            stmt = (
                select(BrowserSessionRecord)
                .where(BrowserSessionRecord.workflow_run_id == workflow_run_id)
                .order_by(BrowserSessionRecord.started_at.desc())
            )
            res = await session.execute(stmt)
            return list(res.scalars().all())

        return await self._execute_with_session(_do)

    async def get_active_sessions(self) -> list[BrowserSessionRecord]:
        """Fetches all currently active or busy browser sessions."""
        async def _do(session: AsyncSession):
            stmt = (
                select(BrowserSessionRecord)
                .where(
                    BrowserSessionRecord.state.in_([
                        BrowserLifecycleState.INITIALIZING.value,
                        BrowserLifecycleState.READY.value,
                        BrowserLifecycleState.BUSY.value,
                    ])
                )
                .order_by(BrowserSessionRecord.started_at.desc())
            )
            res = await session.execute(stmt)
            return list(res.scalars().all())

        return await self._execute_with_session(_do)

