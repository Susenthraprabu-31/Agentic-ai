"""Execution context tracking retry attempts, errors, and deadlines."""

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from app.resilience.error_classifier import ErrorCategory
from app.resilience.retry_policy import RetryPolicy


@dataclass
class RetryAttemptRecord:
    """Detailed record of a single failed execution attempt."""

    attempt_number: int
    error_category: ErrorCategory
    error_type: str
    error_message: str
    retry_delay_seconds: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class RetryContext:
    """
    Maintains in-memory state, history, and timing constraints for an active retry cycle.
    """

    def __init__(
        self,
        operation_name: str,
        policy: RetryPolicy,
        operation_id: str | None = None,
        workflow_run_id: uuid.UUID | None = None,
        node_run_id: uuid.UUID | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.operation_name = operation_name
        self.policy = policy
        self.operation_id = operation_id or str(uuid.uuid4())
        self.workflow_run_id = workflow_run_id
        self.node_run_id = node_run_id
        self.is_cancelled_callback = is_cancelled

        self.attempt_number: int = 0
        self.start_wall_time: float = time.time()
        self.errors: list[RetryAttemptRecord] = []
        self.last_error: Exception | None = None
        self.last_category: ErrorCategory = ErrorCategory.UNCLASSIFIED
        self.is_succeeded: bool = False

    @property
    def elapsed_seconds(self) -> float:
        """Returns total elapsed time in seconds since the first attempt began."""
        return time.time() - self.start_wall_time

    @property
    def remaining_deadline_seconds(self) -> float:
        """Returns remaining seconds before the policy maximum duration is exceeded."""
        if self.policy.max_duration_seconds <= 0:
            return float("inf")
        remaining = self.policy.max_duration_seconds - self.elapsed_seconds
        return max(0.0, remaining)

    def is_cancelled(self) -> bool:
        """Checks if external cancellation has been signaled."""
        if self.is_cancelled_callback is not None:
            try:
                return bool(self.is_cancelled_callback())
            except Exception:
                return False
        return False

    def is_exhausted(self) -> bool:
        """Returns True if attempt count reaches max_attempts or deadline elapsed."""
        if self.attempt_number >= self.policy.max_attempts:
            return True
        if self.policy.max_duration_seconds > 0 and self.remaining_deadline_seconds <= 0.0:
            return True
        return False

    def record_failure(
        self,
        error: Exception,
        category: ErrorCategory,
        delay_seconds: float,
    ) -> RetryAttemptRecord:
        """Records a failed attempt in the history."""
        self.last_error = error
        self.last_category = category

        rec = RetryAttemptRecord(
            attempt_number=self.attempt_number,
            error_category=category,
            error_type=type(error).__name__,
            error_message=str(error),
            retry_delay_seconds=delay_seconds,
        )
        self.errors.append(rec)
        return rec

