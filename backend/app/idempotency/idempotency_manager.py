"""High-level idempotency manager coordinating workflow, node, task, and document deduplication."""

import uuid
from typing import Any, Callable, Coroutine

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.idempotency.idempotency_context import IdempotencyClaim
from app.idempotency.idempotency_key import (
    generate_document_idempotency_key,
    generate_node_idempotency_key,
    generate_task_idempotency_key,
)
from app.idempotency.idempotency_service import IdempotencyService
from app.models.idempotency_record import IdempotencyStatus

logger = get_logger(__name__)


class IdempotencyManager:
    """
    Coordinates idempotent execution guarantees across API endpoints, Celery workers,
    workflow nodes, and document retrieval operations.
    """

    @classmethod
    async def process_workflow_claim(
        cls,
        db: AsyncSession,
        user_id: uuid.UUID,
        workflow_id: uuid.UUID,
        raw_key: str | None,
        payload: Any,
    ) -> IdempotencyClaim | None:
        """
        Processes optional workflow-level idempotency key.
        Returns IdempotencyClaim if key was provided, or None if key was omitted.
        """
        if not raw_key:
            return None

        service = IdempotencyService(db)
        return await service.claim_operation(
            raw_key=raw_key,
            user_id=user_id,
            operation_type="workflow_execute",
            resource_id=str(workflow_id),
            payload=payload,
            ttl_seconds=86400,  # 24 hours
        )

    @classmethod
    async def process_node_claim(
        cls,
        db: AsyncSession,
        workflow_run_id: uuid.UUID,
        node_id: str,
        workflow_version_id: uuid.UUID | None,
        input_data: Any,
    ) -> IdempotencyClaim:
        """Processes node-level duplicate execution protection."""
        service = IdempotencyService(db)
        logical_key = generate_node_idempotency_key(
            workflow_run_id=workflow_run_id,
            node_id=node_id,
            workflow_version_id=workflow_version_id,
            input_data=input_data,
        )
        return await service.claim_operation(
            raw_key=logical_key,
            user_id=None,
            operation_type="node_execute",
            resource_id=f"{workflow_run_id}:{node_id}",
            payload=input_data,
            ttl_seconds=86400,
        )

    @classmethod
    async def process_task_claim(
        cls,
        db: AsyncSession,
        task_name: str,
        workflow_run_id: uuid.UUID,
    ) -> IdempotencyClaim:
        """Prevents duplicate Celery task deliveries from executing twice."""
        service = IdempotencyService(db)
        task_key = generate_task_idempotency_key(task_name, workflow_run_id)
        return await service.claim_operation(
            raw_key=task_key,
            user_id=None,
            operation_type="celery_task",
            resource_id=str(workflow_run_id),
            payload={"task_name": task_name, "workflow_run_id": str(workflow_run_id)},
            ttl_seconds=3600,  # 1 hour lease
        )

    @classmethod
    async def process_document_claim(
        cls,
        db: AsyncSession,
        workflow_run_id: uuid.UUID | None,
        source_url: str,
    ) -> IdempotencyClaim:
        """Prevents duplicate downloads for the same logical document within a workflow execution."""
        service = IdempotencyService(db)
        doc_key = generate_document_idempotency_key(workflow_run_id, source_url)
        return await service.claim_operation(
            raw_key=doc_key,
            user_id=None,
            operation_type="document_retrieve",
            resource_id=source_url[:255],
            payload={"source_url": source_url, "workflow_run_id": str(workflow_run_id or "")},
            ttl_seconds=86400,
        )

    @classmethod
    async def complete_claim(
        cls,
        db: AsyncSession,
        claim_id: uuid.UUID,
        workflow_run_id: uuid.UUID | None = None,
        node_run_id: uuid.UUID | None = None,
        response_status_code: int = 200,
        response_payload: dict[str, Any] | None = None,
    ) -> None:
        """Marks an active claim as completed."""
        service = IdempotencyService(db)
        await service.complete_operation(
            claim_id=claim_id,
            workflow_run_id=workflow_run_id,
            node_run_id=node_run_id,
            response_status_code=response_status_code,
            response_payload=response_payload,
        )

    @classmethod
    async def fail_claim(
        cls,
        db: AsyncSession,
        claim_id: uuid.UUID,
        error_message: str | None = None,
    ) -> None:
        """Marks an active claim as failed to permit safe retry."""
        service = IdempotencyService(db)
        await service.fail_operation(claim_id=claim_id, error_message=error_message)


idempotency_manager = IdempotencyManager()
