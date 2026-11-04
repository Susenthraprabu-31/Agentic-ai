"""Resilience and Retry Engine module."""

from app.resilience.circuit_breaker import CircuitBreaker, circuit_breaker
from app.resilience.error_classifier import ErrorCategory, ErrorClassifier
from app.resilience.exceptions import (
    CircuitBreakerOpenException,
    NonRetryableException,
    ResilienceException,
    RetryAbortedException,
    RetryExhaustedException,
)
from app.resilience.retry_context import RetryContext
from app.resilience.retry_manager import RetryManager, retry_manager, sanitize_sensitive_data
from app.resilience.retry_policy import (
    BROWSER_NAVIGATION_POLICY,
    DATABASE_TRANSIENT_POLICY,
    DOCUMENT_DOWNLOAD_POLICY,
    NO_RETRY_POLICY,
    RetryPolicy,
    get_default_retry_policy,
)

__all__ = [
    "BROWSER_NAVIGATION_POLICY",
    "CircuitBreaker",
    "CircuitBreakerOpenException",
    "DATABASE_TRANSIENT_POLICY",
    "DOCUMENT_DOWNLOAD_POLICY",
    "ErrorCategory",
    "ErrorClassifier",
    "NO_RETRY_POLICY",
    "NonRetryableException",
    "ResilienceException",
    "RetryAbortedException",
    "RetryContext",
    "RetryExhaustedException",
    "RetryManager",
    "RetryPolicy",
    "circuit_breaker",
    "get_default_retry_policy",
    "retry_manager",
    "sanitize_sensitive_data",
]

