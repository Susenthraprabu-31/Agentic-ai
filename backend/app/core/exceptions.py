"""Custom application exceptions with HTTP status code hints."""


class TitleBaseException(Exception):
    """Base exception for all Title Search Platform errors."""

    http_status: int = 500

    def __init__(self, message: str, code: str = "INTERNAL_ERROR") -> None:
        self.message = message
        self.code = code
        super().__init__(message)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(code={self.code!r}, message={self.message!r})"


class NotFoundError(TitleBaseException):
    """Raised when a requested resource does not exist (HTTP 404)."""

    http_status = 404

    def __init__(self, message: str = "Resource not found", code: str = "NOT_FOUND") -> None:
        super().__init__(message=message, code=code)


class ValidationError(TitleBaseException):
    """Raised when input data fails business-rule validation (HTTP 422)."""

    http_status = 422

    def __init__(self, message: str = "Validation error", code: str = "VALIDATION_ERROR") -> None:
        super().__init__(message=message, code=code)


class AuthenticationError(TitleBaseException):
    """Raised when authentication credentials are missing or invalid (HTTP 401)."""

    http_status = 401

    def __init__(self, message: str = "Authentication required", code: str = "UNAUTHENTICATED") -> None:
        super().__init__(message=message, code=code)


class AuthorizationError(TitleBaseException):
    """Raised when the caller lacks permission for an operation (HTTP 403)."""

    http_status = 403

    def __init__(self, message: str = "Permission denied", code: str = "FORBIDDEN") -> None:
        super().__init__(message=message, code=code)


class ConflictError(TitleBaseException):
    """Raised when a create/update would violate a uniqueness constraint (HTTP 409)."""

    http_status = 409

    def __init__(self, message: str = "Resource conflict", code: str = "CONFLICT") -> None:
        super().__init__(message=message, code=code)
