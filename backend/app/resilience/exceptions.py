"""Resilience and Retry Engine custom exceptions."""

from app.core.exceptions import TitleBaseException


class ResilienceException(TitleBaseException):
    """Base exception for all resilience and retry operations."""

    http_status: int = 500

    def __init__(self, message: str, code: str = "RESILIENCE_ERROR") -> None:
        super().__init__(message=message, code=code)


class RetryExhaustedException(ResilienceException):
    """Raised when an operation has exhausted its configured maximum retry attempts or deadline."""

    http_status: int = 504

    def __init__(
        self,
        message: str,
        attempts: int = 0,
        total_duration_seconds: float = 0.0,
        last_error: Exception | None = None,
        code: str = "RETRY_EXHAUSTED",
    ) -> None:
        super().__init__(message=message, code=code)
        self.attempts = attempts
        self.total_duration_seconds = total_duration_seconds
        self.last_error = last_error


class NonRetryableException(ResilienceException):
    """Raised when an operation encounters an immediate non-recoverable failure."""

    http_status: int = 400

    def __init__(
        self,
        message: str,
        original_error: Exception | None = None,
        code: str = "NON_RETRYABLE_ERROR",
    ) -> None:
        super().__init__(message=message, code=code)
        self.original_error = original_error


class RetryAbortedException(ResilienceException):
    """Raised when retries are deliberately aborted due to external signals (e.g. cancellation)."""

    http_status: int = 499  # Client Closed Request

    def __init__(
        self,
        message: str = "Operation retry aborted.",
        reason: str = "cancelled",
        code: str = "RETRY_ABORTED",
    ) -> None:
        super().__init__(message=message, code=code)
        self.reason = reason


class CircuitBreakerOpenException(ResilienceException):
    """Raised when an operation is blocked because the external dependency circuit is OPEN."""

    http_status: int = 503  # Service Unavailable

    def __init__(
        self,
        dependency_key: str,
        open_until: str | None = None,
        reason: str | None = None,
        code: str = "CIRCUIT_BREAKER_OPEN",
    ) -> None:
        msg = f"Circuit breaker for '{dependency_key}' is OPEN. Requests blocked until {open_until or 'reset'}."
        if reason:
            msg += f" Last failure reason: {reason}"
        super().__init__(message=msg, code=code)
        self.dependency_key = dependency_key
        self.open_until = open_until
        self.reason = reason

