"""Asyncio supervisor that runs workflow jobs locally (desktop mode)."""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.services.workflow_execution import run_workflow_in_process
from app.workers.queue import claim_next_queued_run

logger = get_logger(__name__)

_wakeup = asyncio.Event()
_runner_task: asyncio.Task[None] | None = None
_semaphore: asyncio.Semaphore | None = None
_active_tasks: set[asyncio.Task[Any]] = set()
_active_run_id: uuid.UUID | None = None


def active_workflow_run_id() -> str | None:
    return str(_active_run_id) if _active_run_id else None


def notify_job_runner() -> None:
    """Wake the polling loop after a new run is queued."""
    _wakeup.set()


async def _execute_claimed(run_id: uuid.UUID, fencing_token: int) -> None:
    global _active_run_id
    worker_id = f"{socket.gethostname()}:{os.getpid()}:local"
    _active_run_id = run_id
    try:
        await run_workflow_in_process(
            str(run_id),
            fencing_token=fencing_token,
            worker_id=worker_id,
        )
    except Exception as exc:
        logger.exception("Local job runner failed for %s: %s", run_id, exc)
    finally:
        _active_run_id = None


async def _poll_loop() -> None:
    global _semaphore
    concurrency = max(1, int(settings.WORKFLOW_MAX_CONCURRENT_EXECUTIONS))
    _semaphore = asyncio.Semaphore(concurrency)
    logger.info("local_job_runner.started", concurrency=concurrency)

    while True:
        try:
            await asyncio.wait_for(_wakeup.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
        _wakeup.clear()

        async with AsyncSessionLocal() as session:
            run = await claim_next_queued_run(session)
        if not run:
            continue

        async def _run_bounded(rid: uuid.UUID, fence: int) -> None:
            async with _semaphore:
                await _execute_claimed(rid, fence)

        task = asyncio.create_task(_run_bounded(run.id, run.fencing_token or 0))
        _active_tasks.add(task)
        task.add_done_callback(_active_tasks.discard)


async def start_local_job_runner() -> None:
    global _runner_task
    if not settings.JOB_RUNNER_ENABLED:
        return
    if _runner_task and not _runner_task.done():
        return
    _runner_task = asyncio.create_task(_poll_loop())


async def stop_local_job_runner() -> None:
    global _runner_task
    if _runner_task and not _runner_task.done():
        _runner_task.cancel()
        try:
            await _runner_task
        except asyncio.CancelledError:
            pass
    _runner_task = None
    for task in list(_active_tasks):
        task.cancel()
    if _active_tasks:
        await asyncio.gather(*_active_tasks, return_exceptions=True)
    _active_tasks.clear()