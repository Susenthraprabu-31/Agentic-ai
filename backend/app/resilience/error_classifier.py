"""Centralized error classification engine identifying failure recoverability."""

import asyncio
import enum
import re
import socket
from typing import Any, Callable

from app.core.logging import get_logger

logger = get_logger(__name__)


class ErrorCategory(str, enum.Enum):
    """Categorized domain failure types determining retry eligibility."""

    NETWORK_TIMEOUT = "network_timeout"
    CONNECTION_RESET = "connection_reset"
    DNS_FAILURE = "dns_failure"
    HTTP_5XX = "http_5xx"
    HTTP_429 = "http_429"
    HTTP_4XX_CLIENT_ERROR = "http_4xx_client_error"
    HTTP_AUTH_ERROR = "http_auth_error"
    HTTP_FORBIDDEN = "http_forbidden"
    HTTP_NOT_FOUND = "http_not_found"
    SECURITY_CHALLENGE = "security_challenge"
    SSRF_VIOLATION = "ssrf_violation"
    INVALID_RECIPE = "invalid_recipe"
    INVALID_SELECTOR = "invalid_selector"
    INVALID_DOCUMENT = "invalid_document"
    DATABASE_TRANSIENT = "database_transient"
    DATABASE_INTEGRITY = "database_integrity"
    WORKFLOW_CANCELLED = "workflow_cancelled"
    CIRCUIT_OPEN = "circuit_open"
    UNCLASSIFIED = "unclassified"


class ErrorClassifier:
    """
    Evaluates exceptions and response statuses to determine recovery eligibility,
    error categorization, and optional Retry-After delays.
    """

    # Custom handlers: list of tuples (predicate_fn, category, is_retryable)
    _custom_handlers: list[tuple[Callable[[Any], bool], ErrorCategory, bool]] = []

    @classmethod
    def register_handler(
        cls,
        matcher: type[Exception] | Callable[[Any], bool],
        category: ErrorCategory,
        is_retryable: bool,
    ) -> None:
        """Allows domain modules to register custom exception classification rules."""
        if isinstance(matcher, type) and issubclass(matcher, Exception):
            predicate = lambda exc, m=matcher: isinstance(exc, m)
        elif callable(matcher):
            predicate = matcher
        else:
            raise TypeError("matcher must be an Exception subclass or callable predicate.")

        cls._custom_handlers.insert(0, (predicate, category, is_retryable))

    @classmethod
    def parse_retry_after(cls, header_value: str | int | float | None) -> float | None:
        """Parses Retry-After header into seconds if available."""
        if header_value is None:
            return None
        try:
            val_str = str(header_value).strip()
            # If numeric seconds
            if val_str.isdigit() or (val_str.replace(".", "", 1).isdigit()):
                sec = float(val_str)
                return max(0.0, sec)
            # If HTTP date (RFC 7231)
            from email.utils import parsedate_to_datetime
            from datetime import datetime, timezone
            dt = parsedate_to_datetime(val_str)
            now = datetime.now(timezone.utc)
            diff = (dt - now).total_seconds()
            return max(0.0, diff)
        except Exception:
            return None

    @classmethod
    def classify(
        cls,
        exc: Exception | None = None,
        status_code: int | None = None,
        headers: dict[str, Any] | None = None,
    ) -> tuple[ErrorCategory, bool, float | None]:
        """
        Classifies an error or HTTP status code.

        Returns:
            (ErrorCategory, is_retryable: bool, retry_after_seconds: float | None)
        """
        headers_dict = headers or {}
        retry_after = cls.parse_retry_after(
            headers_dict.get("retry-after") or headers_dict.get("Retry-After")
        )

        # 1. Custom registered handlers check first
        if exc is not None:
            for predicate, category, is_retryable in cls._custom_handlers:
                try:
                    if predicate(exc):
                        return category, is_retryable, retry_after
                except Exception:
                    pass

        # 2. Extract HTTP status code from exception if not passed directly
        if status_code is None and exc is not None:
            # Check httpx.HTTPStatusError
            resp = getattr(exc, "response", None)
            if resp is not None:
                status_code = getattr(resp, "status_code", None)
                if retry_after is None:
                    resp_headers = getattr(resp, "headers", {})
                    retry_after = cls.parse_retry_after(
                        resp_headers.get("retry-after") or resp_headers.get("Retry-After")
                    )

            # Check direct status / status_code attribute
            if status_code is None:
                status_code = getattr(exc, "status_code", None) or getattr(exc, "status", None)

        # 3. Classify based on HTTP status codes
        if status_code is not None:
            try:
                code_int = int(status_code)
            except (ValueError, TypeError):
                code_int = 0

            if code_int == 429:
                return ErrorCategory.HTTP_429, True, retry_after
            elif code_int in (500, 502, 503, 504):
                return ErrorCategory.HTTP_5XX, True, retry_after
            elif code_int == 400:
                return ErrorCategory.HTTP_4XX_CLIENT_ERROR, False, None
            elif code_int == 401:
                return ErrorCategory.HTTP_AUTH_ERROR, False, None
            elif code_int == 403:
                return ErrorCategory.HTTP_FORBIDDEN, False, None
            elif code_int == 404:
                return ErrorCategory.HTTP_NOT_FOUND, False, None
            elif 400 <= code_int < 500:
                return ErrorCategory.HTTP_4XX_CLIENT_ERROR, False, None

        # 4. Classify based on Exception Type & Hierarchy
        if exc is None:
            return ErrorCategory.UNCLASSIFIED, False, None

        exc_name = type(exc).__name__
        exc_str = str(exc).lower()

        # A0. Circuit Breaker Open
        if "circuitbreakeropenexception" in exc_name.lower() or "circuit_breaker_open" in exc_str:
            return ErrorCategory.CIRCUIT_OPEN, False, None

        # A. Workflow Cancellation
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)) or "cancell" in exc_str:
            return ErrorCategory.WORKFLOW_CANCELLED, False, None

        # B. Security Barriers (CAPTCHA / Cloudflare / Turnstile)
        if "SecurityChallengeException" in exc_name or "securitychallenge" in exc_name.lower():
            return ErrorCategory.SECURITY_CHALLENGE, False, None
        if "turnstile" in exc_str or "captcha" in exc_str or "cloudflare" in exc_str or "challenge" in exc_str:
            return ErrorCategory.SECURITY_CHALLENGE, False, None

        # C. SSRF Protection Violations
        if "SSRFProtectionException" in exc_name or "ssrf" in exc_str:
            return ErrorCategory.SSRF_VIOLATION, False, None

        # D. Invalid Recipes
        if "RecipeLoadException" in exc_name or "recipeload" in exc_name.lower() or "invalid recipe" in exc_str:
            return ErrorCategory.INVALID_RECIPE, False, None

        # E. Invalid Selector
        if "invalid selector" in exc_str or "syntaxerror" in exc_name.lower() or "is not a valid selector" in exc_str:
            return ErrorCategory.INVALID_SELECTOR, False, None

        # F. Invalid Documents / PDF corruption
        if "ValidationOutcome" in exc_str or "invalid pdf" in exc_str or "corrupt" in exc_str or "encrypted pdf" in exc_str:
            return ErrorCategory.INVALID_DOCUMENT, False, None

        # G. Database Exceptions
        if "IntegrityError" in exc_name:
            return ErrorCategory.DATABASE_INTEGRITY, False, None
        if any(db_err in exc_name for db_err in ("OperationalError", "InterfaceError", "DatabaseError", "DisconnectionError")):
            return ErrorCategory.DATABASE_TRANSIENT, True, None
        if "connection to the database" in exc_str or "deadlock detected" in exc_str:
            return ErrorCategory.DATABASE_TRANSIENT, True, None

        # H. DNS Failures
        if isinstance(exc, socket.gaierror) or "gaierror" in exc_name.lower() or "getaddrinfo failed" in exc_str:
            return ErrorCategory.DNS_FAILURE, True, None

        # I. Network Timeouts
        timeout_indicators = (
            "timeout", "timed out", "timeouterror", "connecttimeout", "readtimeout"
        )
        if any(ind in exc_name.lower() or ind in exc_str for ind in timeout_indicators):
            return ErrorCategory.NETWORK_TIMEOUT, True, None

        # J. Connection Resets / Drops / Refusals
        conn_indicators = (
            "connectionreseterror", "connectionrefusederror", "brokenpipeerror",
            "connection error", "connection reset", "connection refused", "connecterror"
        )
        if any(ind in exc_name.lower() or ind in exc_str for ind in conn_indicators):
            return ErrorCategory.CONNECTION_RESET, True, None

        # Default fallback
        logger.debug("Unclassified error: %s (%s)", exc_name, exc)
        return ErrorCategory.UNCLASSIFIED, False, None

    @classmethod
    def is_circuit_breaker_failure(cls, target: Any) -> bool:
        """
        Determines whether a failure is an infrastructure/dependency failure that should
        count toward tripping the circuit breaker.

        Includes: NETWORK_TIMEOUT, CONNECTION_RESET, DNS_FAILURE, HTTP_5XX, HTTP_429.
        Specifically EXCLUDES:
          - Security challenges (CAPTCHA, Cloudflare, Turnstile)
          - SSRF violations
          - 403 Forbidden / Auth errors
          - Invalid recipe / selector / document
          - Workflow cancellation
          - Already open circuit
        """
        if isinstance(target, ErrorCategory):
            category = target
        elif isinstance(target, Exception):
            category, _, _ = cls.classify(exc=target)
        elif isinstance(target, int):
            category, _, _ = cls.classify(status_code=target)
        else:
            category, _, _ = cls.classify(exc=Exception(str(target)))

        counted_categories = {
            ErrorCategory.NETWORK_TIMEOUT,
            ErrorCategory.CONNECTION_RESET,
            ErrorCategory.DNS_FAILURE,
            ErrorCategory.HTTP_5XX,
            ErrorCategory.HTTP_429,
        }
        return category in counted_categories

