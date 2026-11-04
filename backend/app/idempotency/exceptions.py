"""Idempotency custom exceptions."""

from app.core.exceptions import TitleBaseException


class IdempotencyException(TitleBaseException):
    """Base exception for idempotency engine errors."""

    http_status: int = 500

    def __init__(self, message: str, code: str = "IDEMPOTENCY_ERROR") -> None:
        super().__init__(message=message, code=code)


class IdempotencyConflictError(IdempotencyException):
    """Raised when an idempotency key is reused with a different request payload (HTTP 409)."""

    http_status: int = 409

    def __init__(
        self,
        message: str = "Idempotency key was already used with a different request payload.",
        code: str = "IDEMPOTENCY_PAYLOAD_CONFLICT",
    ) -> None:
        super().__init__(message=message, code=code)


class IdempotencyKeyInvalidError(IdempotencyException):
    """Raised when an idempotency key format or length is invalid (HTTP 400)."""

    http_status: int = 400

    def __init__(
        self,
        message: str = "Invalid Idempotency-Key format or length.",
        code: str = "INVALID_IDEMPOTENCY_KEY",
    ) -> None:
        super().__init__(message=message, code=code)


class IdempotencyClaimLockedError(IdempotencyException):
    """Raised when an operation is currently being processed by another concurrent request/worker."""

    http_status: int = 409

    def __init__(
        self,
        message: str = "Operation is currently being processed by another concurrent request.",
        code: str = "IDEMPOTENCY_CONCURRENT_LOCK",
    ) -> None:
        super().__init__(message=message, code=code)

