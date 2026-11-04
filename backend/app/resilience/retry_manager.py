"""Centralized Retry Manager controlling resilient asynchronous execution."""

import asyncio
import inspect
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.retry_attempt import RetryAttempt, RetryAttemptStatus
from app.resilience.error_classifier import ErrorCategory, ErrorClassifier
from app.resilience.exceptions import (
    CircuitBreakerOpenException,
    NonRetryableException,
    ResilienceException,
    RetryAbortedException,
    RetryExhaustedException,
)
from app.resilience.retry_context import RetryContext
from app.resilience.retry_policy import RetryPolicy, get_default_retry_policy

logger = get_logger(__name__)

T = TypeVar("T")


def sanitize_sensitive_data(text: str | None) -> str:
    """
    Strips passwords, API keys, Bearer tokens, and auth headers from logged and stored error messages.
    """
    if not text:
        return ""
    clean = str(text)
    # Redact Bearer / JWT tokens
    clean = re.sub(r"(?i)bearer\s+[a-zA-Z0-9_\-\.=]+", "Bearer [REDACTED]", clean)
    # Redact connection URI credentials: protocol://user:pass@host
    clean = re.sub(r"://([^:]+):([^@]+)@", r"://\1:[REDACTED]@", clean)
    # Redact common secret params: token=..., password=..., api_key=...
    clean = re.sub(r"(?i)(password|secret|token|api_key|access_token|key)\s*[:=]\s*['\"]?[^'\"&\s,]+['\"]?", r"\1=[REDACTED]", clean)
    # Redact Cookie headers
    clean = re.sub(r"(?i)cookie:\s*[^;\r\n]+", "Cookie: [REDACTED]", clean)
    return clean[:2000]


class RetryManager:
    """
    Orchestrates execution of asynchronous callables under configured retry policies.
    Enforces backoff delays, jitter, cancellation checks, error classification, and history persistence.
    """

    def __init__(self, default_policy: RetryPolicy | None = None) -> None:
        self.default_policy = default_policy or get_default_retry_policy()

    async def execute(
        self,
        operation: Callable[..., Coroutine[Any, Any, T] | T],
        *args: Any,
        policy: RetryPolicy | None = None,
        operation_name: str = "operation",
        operation_id: str | None = None,
        workflow_run_id: uuid.UUID | None = None,
        node_run_id: uuid.UUID | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        db_session: AsyncSession | None = None,
        circuit_key: str | None = None,
        circuit_type: str = "general",
        **kwargs: Any,
    ) -> T:
        """
        Executes operation with automatic retry on transient failures and circuit breaker integration.
        """
        if circuit_key:
            from app.resilience.circuit_breaker import circuit_breaker
            can_exec, reject_reason = await circuit_breaker.can_execute(circuit_key, dependency_type=circuit_type)
            if not can_exec:
                logger.warning(
                    "Operation '%s' blocked by circuit breaker for '%s': %s",
                    operation_name,
                    circuit_key,
                    reject_reason,
                )
                state = await circuit_breaker.get_circuit_state(circuit_key)
                raise CircuitBreakerOpenException(
                    dependency_key=circuit_key,
                    open_until=state.get("open_until"),
                    reason=reject_reason,
                )

        effective_policy = policy or self.default_policy
        ctx = RetryContext(
            operation_name=operation_name,
            policy=effective_policy,
            operation_id=operation_id,
            workflow_run_id=workflow_run_id,
            node_run_id=node_run_id,
            is_cancelled=is_cancelled,
        )

        while True:
            # 1. Check for cancellation before starting an attempt
            if ctx.is_cancelled():
                logger.warning(
                    "Retry cycle aborted for %s (operation_id: %s) due to workflow cancellation.",
                    ctx.operation_name,
                    ctx.operation_id,
                    extra={
                        "workflow_run_id": str(ctx.workflow_run_id) if ctx.workflow_run_id else None,
                        "node_run_id": str(ctx.node_run_id) if ctx.node_run_id else None,
                        "operation_name": ctx.operation_name,
                        "operation_id": ctx.operation_id,
                        "status": "aborted",
                    },
                )
                await self._persist_attempt(
                    ctx=ctx,
                    db=db_session,
                    attempt_num=ctx.attempt_number + 1,
                    category=ErrorCategory.WORKFLOW_CANCELLED,
                    exc=RetryAbortedException("Operation cancelled before execution."),
                    delay=0.0,
                    status=RetryAttemptStatus.aborted.value,
                )
                raise RetryAbortedException(
                    f"Operation '{ctx.operation_name}' cancelled before attempt."
                )

            ctx.attempt_number += 1
            attempt_start = datetime.now(timezone.utc)

            # 2. Execute the operation
            try:
                res = operation(*args, **kwargs)
                if inspect.isawaitable(res):
                    result = await res
                else:
                    result = res

                # Operation succeeded
                ctx.is_succeeded = True
                if circuit_key:
                    from app.resilience.circuit_breaker import circuit_breaker
                    await circuit_breaker.record_success(circuit_key, dependency_type=circuit_type)
                if ctx.attempt_number > 1:
                    logger.info(
                        "Operation '%s' succeeded on attempt %d/%d after transient recovery.",
                        ctx.operation_name,
                        ctx.attempt_number,
                        effective_policy.max_attempts,
                        extra={
                            "workflow_run_id": str(ctx.workflow_run_id) if ctx.workflow_run_id else None,
                            "node_run_id": str(ctx.node_run_id) if ctx.node_run_id else None,
                            "operation_name": ctx.operation_name,
                            "operation_id": ctx.operation_id,
                            "attempt_number": ctx.attempt_number,
                            "final_status": "succeeded",
                        },
                    )
                    await self._persist_attempt(
                        ctx=ctx,
                        db=db_session,
                        attempt_num=ctx.attempt_number,
                        category=ErrorCategory.UNCLASSIFIED,
                        exc=None,
                        delay=0.0,
                        status=RetryAttemptStatus.succeeded.value,
                    )
                return result

            except Exception as exc:
                if circuit_key and not isinstance(exc, CircuitBreakerOpenException):
                    from app.resilience.circuit_breaker import circuit_breaker
                    await circuit_breaker.record_failure(circuit_key, exc, dependency_type=circuit_type)

                # 3. Classify the encountered error
                category, is_retryable, retry_after = ErrorClassifier.classify(exc)

                # Policy-level check override
                if not effective_policy.is_category_allowed(category):
                    is_retryable = False

                # Handle cancellation exceptions directly
                if isinstance(exc, (asyncio.CancelledError, RetryAbortedException)) or category == ErrorCategory.WORKFLOW_CANCELLED:
                    await self._persist_attempt(
                        ctx=ctx,
                        db=db_session,
                        attempt_num=ctx.attempt_number,
                        category=ErrorCategory.WORKFLOW_CANCELLED,
                        exc=exc,
                        delay=0.0,
                        status=RetryAttemptStatus.aborted.value,
                    )
                    raise

                # 4. If error is non-retryable, abort immediately without retrying
                if not is_retryable:
                    logger.warning(
                        "Operation '%s' failed with non-retryable error [%s]: %s",
                        ctx.operation_name,
                        category.value,
                        sanitize_sensitive_data(str(exc)),
                        extra={
                            "workflow_run_id": str(ctx.workflow_run_id) if ctx.workflow_run_id else None,
                            "node_run_id": str(ctx.node_run_id) if ctx.node_run_id else None,
                            "operation_name": ctx.operation_name,
                            "operation_id": ctx.operation_id,
                            "attempt_number": ctx.attempt_number,
                            "error_category": category.value,
                            "final_status": "non_retryable",
                        },
                    )
                    await self._persist_attempt(
                        ctx=ctx,
                        db=db_session,
                        attempt_num=ctx.attempt_number,
                        category=category,
                        exc=exc,
                        delay=0.0,
                        status=RetryAttemptStatus.exhausted.value,
                    )
                    # Preserve original exception
                    raise exc

                # 5. Check if maximum attempts or total duration deadline exceeded
                if ctx.is_exhausted():
                    logger.error(
                        "Operation '%s' exhausted all %d retry attempts. Final error [%s]: %s",
                        ctx.operation_name,
                        effective_policy.max_attempts,
                        category.value,
                        sanitize_sensitive_data(str(exc)),
                        extra={
                            "workflow_run_id": str(ctx.workflow_run_id) if ctx.workflow_run_id else None,
                            "node_run_id": str(ctx.node_run_id) if ctx.node_run_id else None,
                            "operation_name": ctx.operation_name,
                            "operation_id": ctx.operation_id,
                            "attempt_number": ctx.attempt_number,
                            "max_attempts": effective_policy.max_attempts,
                            "error_category": category.value,
                            "final_status": "exhausted",
                        },
                    )
                    await self._persist_attempt(
                        ctx=ctx,
                        db=db_session,
                        attempt_num=ctx.attempt_number,
                        category=category,
                        exc=exc,
                        delay=0.0,
                        status=RetryAttemptStatus.exhausted.value,
                    )
                    # Re-raise original error or raise RetryExhaustedException with cause
                    raise RetryExhaustedException(
                        message=f"Operation '{ctx.operation_name}' failed after {ctx.attempt_number} attempts: {exc}",
                        attempts=ctx.attempt_number,
                        total_duration_seconds=ctx.elapsed_seconds,
                        last_error=exc,
                    ) from exc

                # 6. Calculate backoff delay
                delay = effective_policy.calculate_delay(
                    attempt=ctx.attempt_number,
                    retry_after=retry_after,
                    elapsed_seconds=ctx.elapsed_seconds,
                )

                # Record attempt failure in in-memory context
                ctx.record_failure(error=exc, category=category, delay_seconds=delay)

                logger.warning(
                    "Attempt %d/%d for '%s' failed [%s]. Retrying in %.2fs: %s",
                    ctx.attempt_number,
                    effective_policy.max_attempts,
                    ctx.operation_name,
                    category.value,
                    delay,
                    sanitize_sensitive_data(str(exc)),
                    extra={
                        "workflow_run_id": str(ctx.workflow_run_id) if ctx.workflow_run_id else None,
                        "node_run_id": str(ctx.node_run_id) if ctx.node_run_id else None,
                        "operation_name": ctx.operation_name,
                        "operation_id": ctx.operation_id,
                        "attempt_number": ctx.attempt_number,
                        "max_attempts": effective_policy.max_attempts,
                        "error_category": category.value,
                        "retry_delay_seconds": delay,
                        "final_status": "retrying",
                    },
                )

                # Persist retry attempt to DB if session provided
                await self._persist_attempt(
                    ctx=ctx,
                    db=db_session,
                    attempt_num=ctx.attempt_number,
                    category=category,
                    exc=exc,
                    delay=delay,
                    status=RetryAttemptStatus.retrying.value,
                )

                # 7. Asynchronous non-blocking sleep respecting cancellation
                if delay > 0:
                    sleep_slices = max(1, int(delay / 0.5))
                    slice_duration = delay / sleep_slices
                    for _ in range(sleep_slices):
                        if ctx.is_cancelled():
                            raise RetryAbortedException(
                                f"Operation '{ctx.operation_name}' cancelled during retry backoff."
                            )
                        await asyncio.sleep(slice_duration)

    async def _persist_attempt(
        self,
        ctx: RetryContext,
        db: AsyncSession | None,
        attempt_num: int,
        category: ErrorCategory,
        exc: Exception | None,
        delay: float,
        status: str,
    ) -> None:
        """
        Safely records RetryAttempt to database.
        Never fails or masks the original operation error if persistence encounters an issue.
        """
        if db is None:
            return

        try:
            err_msg = sanitize_sensitive_data(str(exc) if exc else "None")
            attempt_record = RetryAttempt(
                id=uuid.uuid4(),
                workflow_run_id=ctx.workflow_run_id,
                node_run_id=ctx.node_run_id,
                operation_name=ctx.operation_name,
                operation_id=ctx.operation_id,
                attempt_number=attempt_num,
                error_category=category.value,
                error_type=type(exc).__name__ if exc else "None",
                error_message=err_msg,
                retry_delay_seconds=delay,
                status=status,
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc) if status != RetryAttemptStatus.retrying.value else None,
            )
            db.add(attempt_record)
            await db.commit()
        except Exception as persist_err:
            logger.warning(
                "Failed to persist RetryAttempt record to database: %s",
                persist_err,
                exc_info=False,
            )
            try:
                await db.rollback()
            except Exception:
                pass


# Global singleton instance
retry_manager = RetryManager()

