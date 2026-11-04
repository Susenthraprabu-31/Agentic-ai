"""Database persistence and atomic claim coordinator for idempotency records."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.idempotency.exceptions import (
    IdempotencyClaimLockedError,
    IdempotencyConflictError,
    IdempotencyKeyInvalidError,
)
from app.idempotency.idempotency_context import IdempotencyClaim
from app.idempotency.idempotency_key import (
    generate_key_hash,
    generate_payload_hash,
    validate_key_format,
)
from app.models.idempotency_record import IdempotencyRecord, IdempotencyStatus

logger = get_logger(__name__)


class IdempotencyService:
    """Coordinates atomic database claims and lifecycle for idempotent operations."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def claim_operation(
        self,
        raw_key: str,
        user_id: uuid.UUID | None,
        operation_type: str,
        resource_id: str,
        payload: Any,
        ttl_seconds: int = 86400,
    ) -> IdempotencyClaim:
        """
        Attempts to atomically claim an idempotent operation.
        Enforces uniqueness constraint at database level to prevent race conditions.
        """
        if not validate_key_format(raw_key):
            raise IdempotencyKeyInvalidError("Supplied Idempotency-Key is invalid or too long.")

        key_hash = generate_key_hash(raw_key)
        payload_hash = generate_payload_hash(payload)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds)

        user_cond = (
            IdempotencyRecord.user_id == user_id
            if user_id is not None
            else IdempotencyRecord.user_id.is_(None)
        )

        # 1. First check if a record already exists
        check_stmt = select(IdempotencyRecord).where(
            user_cond,
            IdempotencyRecord.idempotency_key_hash == key_hash,
            IdempotencyRecord.operation_type == operation_type,
        )
        res = await self.db.execute(check_stmt)
        existing = res.scalar_one_or_none()

        if existing:
            return await self._handle_existing_record(
                existing=existing,
                payload_hash=payload_hash,
                operation_type=operation_type,
                resource_id=resource_id,
                expires_at=expires_at,
                now=now,
                user_id=user_id,
                key_hash=key_hash,
            )

        new_claim_id = uuid.uuid4()
        record = IdempotencyRecord(
            id=new_claim_id,
            user_id=user_id,
            idempotency_key_hash=key_hash,
            operation_type=operation_type,
            resource_id=resource_id,
            request_payload_hash=payload_hash,
            status=IdempotencyStatus.processing.value,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )

        try:
            # 2. Attempt atomic direct insertion
            self.db.add(record)
            await self.db.flush()

            logger.info(
                "Acquired new idempotency claim %s for user %s op %s",
                new_claim_id,
                user_id,
                operation_type,
            )
            return IdempotencyClaim(
                id=new_claim_id,
                is_replay=False,
                status=IdempotencyStatus.processing,
                operation_type=operation_type,
                resource_id=resource_id,
                expires_at=expires_at,
            )

        except IntegrityError:
            # 3. Concurrency collision or existing claim
            await self.db.rollback()

            res = await self.db.execute(check_stmt)
            existing = res.scalar_one_or_none()

            if not existing:
                raise IdempotencyClaimLockedError("Concurrent claim collision. Please retry.")

            return await self._handle_existing_record(
                existing=existing,
                payload_hash=payload_hash,
                operation_type=operation_type,
                resource_id=resource_id,
                expires_at=expires_at,
                now=now,
                user_id=user_id,
                key_hash=key_hash,
            )

    async def _handle_existing_record(
        self,
        existing: IdempotencyRecord,
        payload_hash: str,
        operation_type: str,
        resource_id: str | None,
        expires_at: datetime,
        now: datetime,
        user_id: uuid.UUID | None,
        key_hash: str,
    ) -> IdempotencyClaim:
        # Check Payload Conflict
        if existing.request_payload_hash != payload_hash:
            logger.warning(
                "Idempotency conflict for user %s key_hash %s: payload mismatch",
                user_id,
                key_hash,
            )
            raise IdempotencyConflictError(
                "Idempotency key was already used with a different request payload."
            )

        existing_status = IdempotencyStatus(existing.status)

        # Case A: Completed -> Idempotent replay
        if existing_status == IdempotencyStatus.completed:
            logger.info(
                "Replaying completed idempotent execution %s for user %s",
                existing.workflow_run_id,
                user_id,
            )
            return IdempotencyClaim(
                id=existing.id,
                is_replay=True,
                status=IdempotencyStatus.completed,
                workflow_run_id=existing.workflow_run_id,
                node_run_id=existing.node_run_id,
                response_status_code=existing.response_status_code,
                response_payload=existing.response_payload,
                expires_at=existing.expires_at,
                operation_type=existing.operation_type,
                resource_id=existing.resource_id,
            )

        # Case B: Failed or Expired -> Allow reclaim/retry
        rec_expires = existing.expires_at
        if rec_expires and rec_expires.tzinfo is None:
            rec_expires = rec_expires.replace(tzinfo=timezone.utc)

        if existing_status == IdempotencyStatus.failed or (rec_expires and rec_expires < now):
            logger.info("Reclaiming expired or failed idempotency record %s", existing.id)
            existing.status = IdempotencyStatus.processing.value
            existing.expires_at = expires_at
            existing.updated_at = now
            await self.db.flush()
            return IdempotencyClaim(
                id=existing.id,
                is_replay=False,
                status=IdempotencyStatus.processing,
                operation_type=operation_type,
                resource_id=resource_id,
                expires_at=expires_at,
            )

        # Case C: Currently processing and not expired
        logger.info("Existing idempotency claim %s is currently processing", existing.id)
        return IdempotencyClaim(
            id=existing.id,
            is_replay=True,
            status=IdempotencyStatus.processing,
            workflow_run_id=existing.workflow_run_id,
            node_run_id=existing.node_run_id,
            expires_at=existing.expires_at,
            operation_type=existing.operation_type,
            resource_id=existing.resource_id,
        )

    async def complete_operation(
        self,
        claim_id: uuid.UUID,
        workflow_run_id: uuid.UUID | None = None,
        node_run_id: uuid.UUID | None = None,
        response_status_code: int = 200,
        response_payload: dict[str, Any] | None = None,
    ) -> None:
        """Transitions claim to COMPLETED state and stores resulting entity references."""
        stmt = select(IdempotencyRecord).where(IdempotencyRecord.id == claim_id)
        res = await self.db.execute(stmt)
        record = res.scalar_one_or_none()
        if record:
            record.status = IdempotencyStatus.completed.value
            if workflow_run_id:
                record.workflow_run_id = workflow_run_id
            if node_run_id:
                record.node_run_id = node_run_id
            record.response_status_code = response_status_code
            if response_payload:
                record.response_payload = response_payload
            record.updated_at = datetime.now(timezone.utc)
            await self.db.flush()

    async def fail_operation(
        self,
        claim_id: uuid.UUID,
        error_message: str | None = None,
    ) -> None:
        """Transitions claim to FAILED state so subsequent requests may legitimately retry."""
        stmt = select(IdempotencyRecord).where(IdempotencyRecord.id == claim_id)
        res = await self.db.execute(stmt)
        record = res.scalar_one_or_none()
        if record:
            record.status = IdempotencyStatus.failed.value
            record.updated_at = datetime.now(timezone.utc)
            if error_message:
                current_payload = record.response_payload or {}
                current_payload["_error"] = str(error_message)[:500]
                record.response_payload = current_payload
            await self.db.flush()

    async def clean_expired_records(self) -> int:
        """Deletes expired idempotency records to maintain table hygiene."""
        now = datetime.now(timezone.utc)
        stmt = delete(IdempotencyRecord).where(IdempotencyRecord.expires_at < now)
        res = await self.db.execute(stmt)
        await self.db.flush()
        return res.rowcount or 0
