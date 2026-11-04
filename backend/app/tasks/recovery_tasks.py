"""Scheduled Celery tasks for worker monitoring, heartbeat inspection, and workflow recovery."""

import asyncio
import os
import socket
from typing import Any

from app.core.logging import get_logger
from app.db.session import WorkerAsyncSessionLocal
from app.services.recovery_service import RecoveryService
from app.services.worker_monitor import WorkerMonitorService
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="worker_heartbeat_check", queue="executions")
def worker_heartbeat_check() -> dict[str, Any]:
    """
    Periodic task checking worker heartbeats and marking unresponsive workers.
    """
    logger.debug("Executing periodic worker heartbeat check.")

    async def _run() -> dict[str, Any]:
        monitor = WorkerMonitorService()
        unresponsive = await monitor.detect_unresponsive_workers()
        health = await monitor.get_worker_health()
        return {
            "unresponsive_count": len(unresponsive),
            "unresponsive_workers": unresponsive,
            "health": health,
        }

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
        logger.exception("Error in worker_heartbeat_check task: %s", exc)
        return {"error": str(exc)}


@celery_app.task(name="recover_orphaned_workflows", queue="executions")
def recover_orphaned_workflows() -> dict[str, Any]:
    """
    Periodic task scanning for orphaned or interrupted workflow executions and claiming recovery.
    """
    logger.debug("Executing periodic orphaned workflow recovery scan.")

    async def _run() -> dict[str, Any]:
        recovery = RecoveryService()
        orphaned = await recovery.detect_orphaned_runs()
        recovered_count = 0
        failed_count = 0
        details: list[dict[str, Any]] = []

        for run in orphaned:
            success, msg = await recovery.claim_and_recover_run(
                run_id=run.id,
                reason="stale_worker_recovery_scheduler",
            )
            if success:
                recovered_count += 1
            else:
                failed_count += 1
            details.append({"run_id": str(run.id), "success": success, "message": msg})

        return {
            "detected_orphaned": len(orphaned),
            "recovered": recovered_count,
            "failed_or_terminal": failed_count,
            "runs": details,
        }

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
        logger.exception("Error in recover_orphaned_workflows task: %s", exc)
        return {"error": str(exc)}


@celery_app.task(name="worker_self_heartbeat", queue="executions")
def worker_self_heartbeat(active_tasks: int = 0) -> bool:
    """
    Called by active worker to refresh its own heartbeat timestamp.
    """
    worker_id = f"{socket.gethostname()}:{os.getpid()}"

    async def _run() -> bool:
        monitor = WorkerMonitorService()
        return await monitor.record_heartbeat(
            worker_id=worker_id,
            active_task_count=active_tasks,
            status="ONLINE" if active_tasks == 0 else "BUSY",
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
        logger.debug("Error in worker_self_heartbeat: %s", exc)
        return False


@celery_app.task(name="cleanup_download_temp_files", queue="executions")
def cleanup_download_temp_files() -> dict[str, Any]:
    """
    Periodic task cleaning up orphaned .part files older than DOCUMENT_TEMP_MAX_AGE_HOURS.
    """
    from app.core.config import settings
    from app.documents.document_service import DocumentService

    removed = DocumentService.cleanup_orphaned_temp_files(max_age_hours=settings.DOCUMENT_TEMP_MAX_AGE_HOURS)
    return {"cleaned_files_count": removed}


@celery_app.task(name="recover_stale_document_downloads", queue="executions")
def recover_stale_document_downloads() -> dict[str, Any]:
    """
    Periodic task recovering document downloads stuck in 'downloading' status.
    """
    from app.core.config import settings
    from app.db.session import WorkerAsyncSessionLocal
    from app.documents.document_service import DocumentService

    async def _run() -> int:
        async with WorkerAsyncSessionLocal() as session:
            try:
                service = DocumentService(db=session)
                count = await service.recover_stale_downloads(
                    stale_threshold_seconds=settings.DOCUMENT_DOWNLOAD_STALE_TIMEOUT_SECONDS
                )
                await session.commit()
                return count
            except Exception:
                await session.rollback()
                raise

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                recovered = pool.submit(lambda: asyncio.run(_run())).result()
        else:
            recovered = asyncio.run(_run())
        return {"recovered_downloads_count": recovered}
    except Exception as exc:
        logger.exception("Error in recover_stale_document_downloads task: %s", exc)
        return {"error": str(exc)}

