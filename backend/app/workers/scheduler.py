"""Periodic maintenance tasks for desktop mode (replaces Celery Beat)."""

from __future__ import annotations

import asyncio
import os
import socket
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import WorkerAsyncSessionLocal
from app.documents.document_service import DocumentService
from app.services.recovery_service import RecoveryService
from app.services.worker_monitor import WorkerMonitorService

logger = get_logger(__name__)

_tasks: list[asyncio.Task[Any]] = []


async def _desktop_worker_heartbeat_tick() -> None:
    """Keep the local job runner visible to worker monitor between workflow runs."""
    worker_id = f"{socket.gethostname()}:{os.getpid()}:local"
    from app.workers import job_runner

    active = len(job_runner._active_tasks)
    current_run = job_runner.active_workflow_run_id()
    monitor = WorkerMonitorService()
    await monitor.record_heartbeat(
        worker_id=worker_id,
        active_task_count=active,
        current_task_id=current_run,
        status="BUSY" if active or current_run else "ONLINE",
    )


async def _heartbeat_monitor_tick() -> None:
    monitor = WorkerMonitorService()
    await monitor.detect_unresponsive_workers()
    await monitor.get_worker_health()


async def _orphan_recovery_tick() -> None:
    recovery = RecoveryService()
    await recovery.finalize_completed_runs_stuck_in_progress()
    orphaned = await recovery.detect_orphaned_runs()
    for run in orphaned:
        try:
            await recovery.claim_and_recover_run(
                run_id=run.id,
                reason="stale_worker_recovery_scheduler",
            )
        except Exception as exc:
            logger.debug("orphan recovery for %s: %s", run.id, exc)


async def _download_cleanup_tick() -> None:
    removed = DocumentService.cleanup_orphaned_temp_files(
        max_age_hours=settings.DOCUMENT_TEMP_MAX_AGE_HOURS
    )
    if removed:
        logger.debug("desktop temp cleanup removed %s files", removed)


async def _stale_downloads_tick() -> None:
    async with WorkerAsyncSessionLocal() as session:
        try:
            service = DocumentService(db=session)
            await service.recover_stale_downloads(
                stale_threshold_seconds=settings.DOCUMENT_DOWNLOAD_STALE_TIMEOUT_SECONDS
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _interval_loop(
    tick: str,
    coro,
    interval: float,
) -> None:
    while True:
        try:
            await coro()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("desktop %s tick: %s", tick, exc)
        await asyncio.sleep(interval)


async def start_local_scheduler() -> None:
    if not settings.JOB_RUNNER_ENABLED:
        return
    _tasks.extend(
        [
            asyncio.create_task(
                _interval_loop(
                    "local_worker_heartbeat",
                    _desktop_worker_heartbeat_tick,
                    float(settings.WORKER_HEARTBEAT_INTERVAL_SECONDS),
                )
            ),
            asyncio.create_task(
                _interval_loop(
                    "heartbeat_monitor",
                    _heartbeat_monitor_tick,
                    float(settings.WORKER_MONITOR_INTERVAL_SECONDS),
                )
            ),
            asyncio.create_task(
                _interval_loop(
                    "orphan_recovery",
                    _orphan_recovery_tick,
                    float(settings.EXECUTION_RECOVERY_INTERVAL_SECONDS),
                )
            ),
            asyncio.create_task(
                _interval_loop(
                    "temp_cleanup",
                    _download_cleanup_tick,
                    float(settings.DOCUMENT_TEMP_CLEANUP_INTERVAL_SECONDS),
                )
            ),
            asyncio.create_task(
                _interval_loop(
                    "stale_downloads",
                    _stale_downloads_tick,
                    float(settings.DOCUMENT_DOWNLOAD_STALE_TIMEOUT_SECONDS),
                )
            ),
        ]
    )


async def stop_local_scheduler() -> None:
    for task in _tasks:
        if not task.done():
            task.cancel()
    if _tasks:
        await asyncio.gather(*_tasks, return_exceptions=True)
    _tasks.clear()