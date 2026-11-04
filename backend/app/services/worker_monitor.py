"""Worker monitoring and heartbeat service tracking background Celery worker lifecycle."""

from datetime import datetime, timedelta, timezone
import json
import os
import socket
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.worker import WorkerRecord, WorkerStatus

logger = get_logger(__name__)


class WorkerMonitorService:
    """
    Coordinates worker registration, periodic heartbeats, unresponsiveness detection,
    and worker health aggregation.
    """

    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db

    async def _get_redis(self) -> Any | None:
        try:
            import redis.asyncio as aioredis
            client = aioredis.from_url(
                settings.REDIS_URL,
                socket_connect_timeout=1,
                socket_timeout=1,
                decode_responses=True,
            )
            await client.ping()
            return client
        except Exception:
            return None

    async def register_worker(
        self,
        worker_id: str,
        hostname: str | None = None,
        pid: int | None = None,
        queues: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkerRecord:
        """Registers a worker on startup."""
        host = hostname or socket.gethostname()
        process_id = pid or os.getpid()
        queue_list = queues or ["executions", "ai"]
        now = datetime.now(timezone.utc)

        logger.info("Registering worker %s (host=%s, pid=%d)", worker_id, host, process_id)

        session = self.db or AsyncSessionLocal()
        try:
            stmt = select(WorkerRecord).where(WorkerRecord.worker_id == worker_id)
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()

            if not record:
                record = WorkerRecord(
                    worker_id=worker_id,
                    hostname=host,
                    pid=process_id,
                    queues=queue_list,
                    status=WorkerStatus.ONLINE.value,
                    active_task_count=0,
                    started_at=now,
                    last_heartbeat_at=now,
                    stopped_at=None,
                    worker_metadata=metadata or {},
                )
                session.add(record)
            else:
                record.hostname = host
                record.pid = process_id
                record.queues = queue_list
                record.status = WorkerStatus.ONLINE.value
                record.active_task_count = 0
                record.started_at = now
                record.last_heartbeat_at = now
                record.stopped_at = None
                if metadata:
                    record.worker_metadata = metadata

            await session.commit()
            await session.refresh(record)

            # Mirror to Redis
            redis_client = await self._get_redis()
            if redis_client:
                try:
                    await redis_client.set(
                        f"worker:{worker_id}:heartbeat",
                        json.dumps({
                            "worker_id": worker_id,
                            "status": WorkerStatus.ONLINE.value,
                            "last_heartbeat_at": now.isoformat(),
                            "active_task_count": 0,
                        }),
                        ex=settings.WORKER_HEARTBEAT_TIMEOUT_SECONDS * 2,
                    )
                finally:
                    await redis_client.aclose()

            return record
        finally:
            if not self.db:
                await session.close()

    async def record_heartbeat(
        self,
        worker_id: str,
        active_task_count: int = 0,
        current_task_id: str | None = None,
        status: str | None = None,
    ) -> bool:
        """
        Records a periodic worker heartbeat.
        Guards against stale heartbeats resurrecting an OFFLINE or RECOVERING worker.
        """
        now = datetime.now(timezone.utc)

        # 1. Update Redis heartbeat key
        redis_client = await self._get_redis()
        if redis_client:
            try:
                await redis_client.set(
                    f"worker:{worker_id}:heartbeat",
                    json.dumps({
                        "worker_id": worker_id,
                        "status": status or WorkerStatus.ONLINE.value,
                        "last_heartbeat_at": now.isoformat(),
                        "active_task_count": active_task_count,
                        "current_task_id": current_task_id,
                    }),
                    ex=settings.WORKER_HEARTBEAT_TIMEOUT_SECONDS * 2,
                )
            except Exception as exc:
                logger.debug("Redis heartbeat write error: %s", exc)
            finally:
                await redis_client.aclose()

        # 2. Update database record
        session = self.db or AsyncSessionLocal()
        try:
            stmt = select(WorkerRecord).where(WorkerRecord.worker_id == worker_id)
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()

            if not record:
                # Register worker dynamically if record absent
                record = WorkerRecord(
                    worker_id=worker_id,
                    hostname=socket.gethostname(),
                    pid=os.getpid(),
                    queues=["executions"],
                    status=status or WorkerStatus.ONLINE.value,
                    active_task_count=active_task_count,
                    current_task_id=current_task_id,
                    started_at=now,
                    last_heartbeat_at=now,
                )
                session.add(record)
            else:
                # Guard: Do not allow a late heartbeat to overwrite a worker explicitly marked OFFLINE
                if record.status == WorkerStatus.OFFLINE.value:
                    logger.warning("Rejecting heartbeat for worker %s already marked OFFLINE", worker_id)
                    return False

                # Ensure heartbeat is newer
                rec_dt = record.last_heartbeat_at
                if rec_dt and rec_dt.tzinfo is None:
                    rec_dt = rec_dt.replace(tzinfo=timezone.utc)
                if rec_dt and rec_dt > now:
                    return False

                record.last_heartbeat_at = now
                record.active_task_count = active_task_count
                record.current_task_id = current_task_id
                if status:
                    record.status = status
                elif record.status == WorkerStatus.UNRESPONSIVE.value:
                    record.status = WorkerStatus.ONLINE.value

            await session.commit()
            return True
        finally:
            if not self.db:
                await session.close()

    async def mark_worker_offline(self, worker_id: str) -> None:
        """Marks worker as gracefully shut down and OFFLINE."""
        now = datetime.now(timezone.utc)
        logger.info("Marking worker %s as OFFLINE", worker_id)

        session = self.db or AsyncSessionLocal()
        try:
            stmt = select(WorkerRecord).where(WorkerRecord.worker_id == worker_id)
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if record:
                record.status = WorkerStatus.OFFLINE.value
                record.stopped_at = now
                record.active_task_count = 0
                record.current_task_id = None
                await session.commit()

            redis_client = await self._get_redis()
            if redis_client:
                try:
                    await redis_client.delete(f"worker:{worker_id}:heartbeat")
                finally:
                    await redis_client.aclose()
        finally:
            if not self.db:
                await session.close()

    async def detect_unresponsive_workers(self) -> list[str]:
        """
        Identifies active workers whose heartbeat is older than WORKER_HEARTBEAT_TIMEOUT_SECONDS
        and transitions them to UNRESPONSIVE.
        """
        timeout_seconds = settings.WORKER_HEARTBEAT_TIMEOUT_SECONDS
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
        unresponsive_ids: list[str] = []

        session = self.db or AsyncSessionLocal()
        try:
            # Query workers currently marked ONLINE or BUSY with stale heartbeat
            stmt = select(WorkerRecord).where(
                WorkerRecord.status.in_([WorkerStatus.ONLINE.value, WorkerStatus.BUSY.value]),
                WorkerRecord.last_heartbeat_at < cutoff,
            )
            res = await session.execute(stmt)
            workers = res.scalars().all()

            for w in workers:
                logger.warning(
                    "Worker %s missed heartbeats beyond %ds timeout. Marking UNRESPONSIVE.",
                    w.worker_id,
                    timeout_seconds,
                    extra={"worker_id": w.worker_id, "last_heartbeat": str(w.last_heartbeat_at)},
                )
                w.status = WorkerStatus.UNRESPONSIVE.value
                unresponsive_ids.append(w.worker_id)

            if unresponsive_ids:
                await session.commit()

            return unresponsive_ids
        finally:
            if not self.db:
                await session.close()

    async def get_workers(self) -> list[dict[str, Any]]:
        """Returns all registered workers with their live states."""
        session = self.db or AsyncSessionLocal()
        try:
            stmt = select(WorkerRecord).order_by(WorkerRecord.last_heartbeat_at.desc())
            res = await session.execute(stmt)
            records = res.scalars().all()
            now = datetime.now(timezone.utc)

            workers = []
            for r in records:
                lh = r.last_heartbeat_at
                if lh and lh.tzinfo is None:
                    lh = lh.replace(tzinfo=timezone.utc)
                age_seconds = int((now - lh).total_seconds()) if lh else None

                workers.append({
                    "worker_id": r.worker_id,
                    "hostname": r.hostname,
                    "pid": r.pid,
                    "queues": r.queues or [],
                    "status": r.status,
                    "active_task_count": r.active_task_count,
                    "current_task_id": r.current_task_id,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "registered_at": r.started_at.isoformat() if r.started_at else None,
                    "last_heartbeat_at": r.last_heartbeat_at.isoformat() if r.last_heartbeat_at else None,
                    "stopped_at": r.stopped_at.isoformat() if r.stopped_at else None,
                    "heartbeat_age_seconds": age_seconds,
                })
            return workers
        finally:
            if not self.db:
                await session.close()

    async def get_worker(self, worker_id: str) -> dict[str, Any] | None:
        """Returns details for a single worker."""
        session = self.db or AsyncSessionLocal()
        try:
            stmt = select(WorkerRecord).where(WorkerRecord.worker_id == worker_id)
            res = await session.execute(stmt)
            r = res.scalar_one_or_none()
            if not r:
                return None
            now = datetime.now(timezone.utc)
            lh = r.last_heartbeat_at
            if lh and lh.tzinfo is None:
                lh = lh.replace(tzinfo=timezone.utc)
            age_seconds = int((now - lh).total_seconds()) if lh else None

            return {
                "worker_id": r.worker_id,
                "hostname": r.hostname,
                "pid": r.pid,
                "queues": r.queues or [],
                "status": r.status,
                "active_task_count": r.active_task_count,
                "current_task_id": r.current_task_id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "last_heartbeat_at": r.last_heartbeat_at.isoformat() if r.last_heartbeat_at else None,
                "stopped_at": r.stopped_at.isoformat() if r.stopped_at else None,
                "heartbeat_age_seconds": age_seconds,
                "worker_metadata": r.worker_metadata or {},
            }
        finally:
            if not self.db:
                await session.close()

    async def get_worker_health(self) -> dict[str, Any]:
        """Returns aggregate cluster worker health."""
        session = self.db or AsyncSessionLocal()
        try:
            workers = await self.get_workers()
            total = len(workers)
            online = sum(1 for w in workers if w["status"] == WorkerStatus.ONLINE.value)
            busy = sum(1 for w in workers if w["status"] == WorkerStatus.BUSY.value)
            unresponsive = sum(1 for w in workers if w["status"] == WorkerStatus.UNRESPONSIVE.value)
            offline = sum(1 for w in workers if w["status"] == WorkerStatus.OFFLINE.value)
            active_tasks = sum(w["active_task_count"] for w in workers)

            overall_status = "HEALTHY"
            if unresponsive > 0 or (online + busy == 0 and total > 0):
                overall_status = "DEGRADED" if (online + busy > 0) else "UNHEALTHY"

            return {
                "overall_status": overall_status,
                "total_workers": total,
                "online_workers": online,
                "busy_workers": busy,
                "unresponsive_workers": unresponsive,
                "offline_workers": offline,
                "total_active_tasks": active_tasks,
                "active_tasks": active_tasks,
                "heartbeat_timeout_seconds": settings.WORKER_HEARTBEAT_TIMEOUT_SECONDS,
                "workers": workers,
            }
        finally:
            if not self.db:
                await session.close()

