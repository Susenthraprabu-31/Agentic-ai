"""Configurable retry policies and exponential backoff calculator."""

import random
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.resilience.error_classifier import ErrorCategory


@dataclass
class RetryPolicy:
    """
    Defines configurable retry constraints and delay parameters for an operation.
    """

    name: str = "default"
    max_attempts: int = 3
    initial_delay_seconds: float = 2.0
    max_delay_seconds: float = 60.0
    exponential_base: float = 2.0
    jitter_enabled: bool = True
    max_duration_seconds: float = 300.0
    max_retry_after_seconds: float = 120.0
    retryable_categories: set[ErrorCategory] | None = None
    non_retryable_categories: set[ErrorCategory] | None = None

    def calculate_delay(
        self,
        attempt: int,
        retry_after: float | None = None,
        elapsed_seconds: float = 0.0,
    ) -> float:
        """
        Calculates the sleep delay for the current retry attempt.
        Applies exponential backoff, respects Retry-After headers, incorporates bounded jitter,
        and strictly clamps the delay to avoid exceeding max_delay or remaining deadline.
        """
        # 1. Base exponential calculation
        # attempt 1 means 1st retry -> initial_delay * (base ** 0) = initial_delay
        attempt_idx = max(1, attempt)
        calculated = self.initial_delay_seconds * (self.exponential_base ** (attempt_idx - 1))
        delay = min(calculated, self.max_delay_seconds)

        # 2. Incorporate Retry-After header if provided and valid
        if retry_after is not None and retry_after > 0:
            capped_retry_after = min(retry_after, self.max_retry_after_seconds)
            # Use the larger of calculated backoff or Retry-After
            delay = max(delay, capped_retry_after)

        # 3. Apply randomized jitter within a bounded range
        if self.jitter_enabled and delay > 0:
            # Jitter within 75% to 125% of delay
            delay = random.uniform(0.75 * delay, 1.25 * delay)

        # 4. Enforce strict upper bound
        delay = min(delay, self.max_delay_seconds)

        # 5. Clamp to remaining overall duration deadline if configured
        if self.max_duration_seconds > 0:
            remaining = max(0.0, self.max_duration_seconds - elapsed_seconds)
            delay = min(delay, remaining)

        return max(0.0, round(delay, 3))

    def is_category_allowed(self, category: ErrorCategory) -> bool:
        """Checks if an error category is eligible under this specific policy."""
        if self.non_retryable_categories and category in self.non_retryable_categories:
            return False
        if self.retryable_categories is not None:
            return category in self.retryable_categories
        return True


# ---------------------------------------------------------------------------
# Predefined Standard Operation Policies
# ---------------------------------------------------------------------------

def get_default_retry_policy() -> RetryPolicy:
    """Builds the default policy using application settings."""
    return RetryPolicy(
        name="default",
        max_attempts=getattr(settings, "RETRY_MAX_ATTEMPTS", 3),
        initial_delay_seconds=float(getattr(settings, "RETRY_INITIAL_DELAY_SECONDS", 2.0)),
        max_delay_seconds=float(getattr(settings, "RETRY_MAX_DELAY_SECONDS", 60.0)),
        exponential_base=float(getattr(settings, "RETRY_EXPONENTIAL_BASE", 2.0)),
        jitter_enabled=bool(getattr(settings, "RETRY_JITTER_ENABLED", True)),
        max_duration_seconds=float(getattr(settings, "RETRY_MAX_DURATION_SECONDS", 300.0)),
    )


BROWSER_NAVIGATION_POLICY = RetryPolicy(
    name="browser_navigation",
    max_attempts=3,
    initial_delay_seconds=2.0,
    max_delay_seconds=30.0,
    exponential_base=2.0,
    jitter_enabled=True,
    max_duration_seconds=120.0,
    retryable_categories={
        ErrorCategory.NETWORK_TIMEOUT,
        ErrorCategory.CONNECTION_RESET,
        ErrorCategory.DNS_FAILURE,
        ErrorCategory.HTTP_5XX,
    },
    non_retryable_categories={
        ErrorCategory.SECURITY_CHALLENGE,
        ErrorCategory.SSRF_VIOLATION,
        ErrorCategory.INVALID_RECIPE,
        ErrorCategory.INVALID_SELECTOR,
        ErrorCategory.HTTP_AUTH_ERROR,
        ErrorCategory.HTTP_FORBIDDEN,
    },
)

DOCUMENT_DOWNLOAD_POLICY = RetryPolicy(
    name="document_download",
    max_attempts=3,
    initial_delay_seconds=1.5,
    max_delay_seconds=30.0,
    exponential_base=2.0,
    jitter_enabled=True,
    max_duration_seconds=120.0,
    retryable_categories={
        ErrorCategory.NETWORK_TIMEOUT,
        ErrorCategory.CONNECTION_RESET,
        ErrorCategory.HTTP_5XX,
        ErrorCategory.HTTP_429,
    },
    non_retryable_categories={
        ErrorCategory.SECURITY_CHALLENGE,
        ErrorCategory.SSRF_VIOLATION,
        ErrorCategory.INVALID_DOCUMENT,
        ErrorCategory.HTTP_NOT_FOUND,
        ErrorCategory.HTTP_AUTH_ERROR,
        ErrorCategory.HTTP_FORBIDDEN,
    },
)

DATABASE_TRANSIENT_POLICY = RetryPolicy(
    name="database_transient",
    max_attempts=3,
    initial_delay_seconds=0.5,
    max_delay_seconds=5.0,
    exponential_base=2.0,
    jitter_enabled=True,
    max_duration_seconds=15.0,
    retryable_categories={
        ErrorCategory.DATABASE_TRANSIENT,
    },
    non_retryable_categories={
        ErrorCategory.DATABASE_INTEGRITY,
    },
)

NO_RETRY_POLICY = RetryPolicy(
    name="no_retry",
    max_attempts=1,
    initial_delay_seconds=0.0,
    max_delay_seconds=0.0,
    jitter_enabled=False,
)

