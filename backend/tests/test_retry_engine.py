"""Unit and integration tests for Phase 3D.1 Resilience & Retry Engine.

Covers all 22 required test scenarios:
1. Successful operation without retries
2. Temporary network failure followed by success
3. Retry exhaustion
4. Exponential backoff calculation
5. Jitter bounds
6. Maximum delay enforcement
7. Maximum total retry duration
8. HTTP 429 with Retry-After
9. HTTP 503 retry
10. HTTP 403 no retry
11. CAPTCHA no retry
12. Cloudflare challenge no retry
13. SSRF violation no retry
14. Invalid PDF no retry
15. Workflow cancellation during retry
16. Asynchronous operation support
17. Original exception preservation
18. Retry history persistence
19. Retry history persistence failure does not mask original error
20. Document download retry integration
21. Browser navigation retry integration
22. Database transient error handling
"""

import asyncio
import io
import os
import shutil
import tempfile
import uuid
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select

from app.browser.security_detector import SecurityDetectionResult
from app.browser.step_runner import PlaywrightStepRunner, SecurityChallengeException
from app.browser.url_validator import SSRFProtectionException
from app.db.base import Base
from app.documents.document_downloader import DocumentDownloader
from app.models import (
    AssessorResult,
    CountySource,
    DocumentArtifact,
    NodeRun,
    Recipe,
    RecipeVersion,
    RefreshToken,
    RetryAttempt,
    SecurityEvent,
    SourceConfiguration,
    TaxResult,
    TemplateVersion,
    UITemplate,
    User,
    ValidationCase,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
)
from app.resilience.error_classifier import ErrorCategory, ErrorClassifier
from app.resilience.exceptions import (
    NonRetryableException,
    RetryAbortedException,
    RetryExhaustedException,
)
from app.resilience.retry_context import RetryContext
from app.resilience.retry_manager import RetryManager, retry_manager, sanitize_sensitive_data
from app.resilience.retry_policy import (
    BROWSER_NAVIGATION_POLICY,
    DATABASE_TRANSIENT_POLICY,
    DOCUMENT_DOWNLOAD_POLICY,
    RetryPolicy,
)

# ---------------------------------------------------------------------------
# Test Database Setup
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

ALL_TABLES = [
    User.__table__,
    RefreshToken.__table__,
    CountySource.__table__,
    UITemplate.__table__,
    TemplateVersion.__table__,
    SourceConfiguration.__table__,
    Recipe.__table__,
    RecipeVersion.__table__,
    ValidationCase.__table__,
    Workflow.__table__,
    WorkflowVersion.__table__,
    WorkflowRun.__table__,
    NodeRun.__table__,
    AssessorResult.__table__,
    TaxResult.__table__,
    SecurityEvent.__table__,
    DocumentArtifact.__table__,
    RetryAttempt.__table__,
]


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=ALL_TABLES))
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=ALL_TABLES))


# ---------------------------------------------------------------------------
# 1. Successful Operation Without Retries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_operation_without_retries():
    attempts = 0

    async def op():
        nonlocal attempts
        attempts += 1
        return "success_val"

    policy = RetryPolicy(max_attempts=3, initial_delay_seconds=0.01)
    mgr = RetryManager(default_policy=policy)

    result = await mgr.execute(op, operation_name="test_success")
    assert result == "success_val"
    assert attempts == 1


# ---------------------------------------------------------------------------
# 2. Temporary Network Failure Followed by Success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_temporary_network_failure_followed_by_success():
    attempts = 0

    async def op():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectTimeout("Connection timed out")
        return "recovered"

    policy = RetryPolicy(max_attempts=3, initial_delay_seconds=0.01, jitter_enabled=False)
    mgr = RetryManager(default_policy=policy)

    result = await mgr.execute(op, operation_name="test_recover")
    assert result == "recovered"
    assert attempts == 2


# ---------------------------------------------------------------------------
# 3. Retry Exhaustion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_exhaustion():
    attempts = 0

    async def failing_op():
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("Socket read timed out")

    policy = RetryPolicy(max_attempts=3, initial_delay_seconds=0.01, jitter_enabled=False)
    mgr = RetryManager(default_policy=policy)

    with pytest.raises(RetryExhaustedException) as exc_info:
        await mgr.execute(failing_op, operation_name="test_exhaust")

    assert attempts == 3
    assert exc_info.value.attempts == 3
    assert isinstance(exc_info.value.last_error, httpx.ReadTimeout)


# ---------------------------------------------------------------------------
# 4. Exponential Backoff Calculation
# ---------------------------------------------------------------------------


def test_exponential_backoff_calculation():
    policy = RetryPolicy(
        initial_delay_seconds=2.0,
        exponential_base=2.0,
        max_delay_seconds=60.0,
        jitter_enabled=False,
    )

    # Attempt 1: 2.0 * (2 ** 0) = 2.0
    assert policy.calculate_delay(attempt=1) == 2.0
    # Attempt 2: 2.0 * (2 ** 1) = 4.0
    assert policy.calculate_delay(attempt=2) == 4.0
    # Attempt 3: 2.0 * (2 ** 2) = 8.0
    assert policy.calculate_delay(attempt=3) == 8.0
    # Attempt 4: 2.0 * (2 ** 3) = 16.0
    assert policy.calculate_delay(attempt=4) == 16.0


# ---------------------------------------------------------------------------
# 5. Jitter Bounds
# ---------------------------------------------------------------------------


def test_jitter_bounds():
    policy = RetryPolicy(
        initial_delay_seconds=10.0,
        exponential_base=2.0,
        max_delay_seconds=100.0,
        jitter_enabled=True,
    )

    # Base for attempt 1 is 10.0. Jitter should be within [0.75 * 10, 1.25 * 10] = [7.5, 12.5]
    for _ in range(50):
        delay = policy.calculate_delay(attempt=1)
        assert 7.5 <= delay <= 12.5


# ---------------------------------------------------------------------------
# 6. Maximum Delay Enforcement
# ---------------------------------------------------------------------------


def test_maximum_delay_enforcement():
    policy = RetryPolicy(
        initial_delay_seconds=10.0,
        exponential_base=2.0,
        max_delay_seconds=25.0,
        jitter_enabled=False,
    )

    # Attempt 3 without max_delay would be 40.0s, but should be clamped to 25.0s
    assert policy.calculate_delay(attempt=3) == 25.0
    # Attempt 5 would be 160.0s -> clamped to 25.0s
    assert policy.calculate_delay(attempt=5) == 25.0


# ---------------------------------------------------------------------------
# 7. Maximum Total Retry Duration
# ---------------------------------------------------------------------------


def test_maximum_total_retry_duration():
    policy = RetryPolicy(
        initial_delay_seconds=10.0,
        max_delay_seconds=30.0,
        max_duration_seconds=5.0,
        jitter_enabled=False,
    )

    # If 4.2 seconds have elapsed, delay must be clamped to remaining 0.8s
    delay = policy.calculate_delay(attempt=1, elapsed_seconds=4.2)
    assert delay == 0.8

    # If 5.0 seconds have elapsed, remaining is 0.0s
    assert policy.calculate_delay(attempt=1, elapsed_seconds=5.0) == 0.0


# ---------------------------------------------------------------------------
# 8. HTTP 429 with Retry-After
# ---------------------------------------------------------------------------


def test_http_429_with_retry_after():
    headers = {"Retry-After": "15"}
    cat, retryable, delay = ErrorClassifier.classify(status_code=429, headers=headers)

    assert cat == ErrorCategory.HTTP_429
    assert retryable is True
    assert delay == 15.0

    policy = RetryPolicy(initial_delay_seconds=2.0, jitter_enabled=False)
    calc_delay = policy.calculate_delay(attempt=1, retry_after=delay)
    assert calc_delay == 15.0


# ---------------------------------------------------------------------------
# 9. HTTP 503 Retry
# ---------------------------------------------------------------------------


def test_http_503_retry():
    cat, retryable, delay = ErrorClassifier.classify(status_code=503)
    assert cat == ErrorCategory.HTTP_5XX
    assert retryable is True


# ---------------------------------------------------------------------------
# 10. HTTP 403 No Retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_403_no_retry():
    attempts = 0

    async def forbidden_op():
        nonlocal attempts
        attempts += 1
        req = httpx.Request("GET", "https://county.gov/private")
        resp = httpx.Response(403, request=req)
        raise httpx.HTTPStatusError("Forbidden", request=req, response=resp)

    mgr = RetryManager()
    with pytest.raises(httpx.HTTPStatusError):
        await mgr.execute(forbidden_op)

    # Must abort immediately on 403 without retrying
    assert attempts == 1


# ---------------------------------------------------------------------------
# 11. CAPTCHA No Retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_captcha_no_retry():
    attempts = 0

    async def captcha_op():
        nonlocal attempts
        attempts += 1
        res = SecurityDetectionResult(
            is_detected=True,
            event_type="captcha",
            url="https://county.gov/search",
            reason="Google reCAPTCHA v2 checkbox detected in DOM",
        )
        raise SecurityChallengeException(res)

    mgr = RetryManager()
    with pytest.raises(SecurityChallengeException):
        await mgr.execute(captcha_op)

    assert attempts == 1


# ---------------------------------------------------------------------------
# 12. Cloudflare Challenge No Retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cloudflare_challenge_no_retry():
    attempts = 0

    async def cloudflare_op():
        nonlocal attempts
        attempts += 1
        res = SecurityDetectionResult(
            is_detected=True,
            event_type="cloudflare",
            url="https://county.gov/search",
            reason="Cloudflare Turnstile verification challenge detected",
        )
        raise SecurityChallengeException(res)

    mgr = RetryManager()
    with pytest.raises(SecurityChallengeException):
        await mgr.execute(cloudflare_op)

    assert attempts == 1


# ---------------------------------------------------------------------------
# 13. SSRF Violation No Retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ssrf_violation_no_retry():
    attempts = 0

    async def ssrf_op():
        nonlocal attempts
        attempts += 1
        raise SSRFProtectionException("Prohibited cloud metadata 169.254.169.254 requested")

    mgr = RetryManager()
    with pytest.raises(SSRFProtectionException):
        await mgr.execute(ssrf_op)

    assert attempts == 1


# ---------------------------------------------------------------------------
# 14. Invalid PDF No Retry
# ---------------------------------------------------------------------------


def test_invalid_pdf_no_retry():
    err = ValueError("Invalid PDF: magic bytes missing or corrupted structure")
    cat, retryable, _ = ErrorClassifier.classify(err)
    assert cat == ErrorCategory.INVALID_DOCUMENT
    assert retryable is False


# ---------------------------------------------------------------------------
# 15. Workflow Cancellation During Retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_cancellation_during_retry():
    cancelled = False
    attempts = 0

    async def cancellable_op():
        nonlocal attempts, cancelled
        attempts += 1
        if attempts == 1:
            # Signal external cancellation before second attempt
            cancelled = True
            raise httpx.ConnectTimeout("Timeout 1")
        return "should_not_reach"

    policy = RetryPolicy(max_attempts=3, initial_delay_seconds=0.1)
    mgr = RetryManager(default_policy=policy)

    with pytest.raises(RetryAbortedException) as exc_info:
        await mgr.execute(
            cancellable_op,
            is_cancelled=lambda: cancelled,
            operation_name="cancel_test",
        )

    assert attempts == 1
    assert "cancelled" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 16. Asynchronous Operation Support
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asynchronous_operation_support():
    async def async_calc(x: int, y: int, multiplier: int = 1) -> int:
        await asyncio.sleep(0.001)
        return (x + y) * multiplier

    mgr = RetryManager()
    val = await mgr.execute(async_calc, 10, 20, multiplier=2)
    assert val == 60


# ---------------------------------------------------------------------------
# 17. Original Exception Preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_original_exception_preservation():
    class SpecificCustomError(Exception):
        pass

    async def fail():
        raise SpecificCustomError("Detailed custom message preserved")

    policy = RetryPolicy(max_attempts=2, initial_delay_seconds=0.01)
    # Register custom error as retryable
    ErrorClassifier.register_handler(SpecificCustomError, ErrorCategory.NETWORK_TIMEOUT, is_retryable=True)

    mgr = RetryManager(default_policy=policy)
    with pytest.raises(RetryExhaustedException) as exc_info:
        await mgr.execute(fail)

    assert isinstance(exc_info.value.last_error, SpecificCustomError)
    assert "Detailed custom message preserved" in str(exc_info.value.last_error)


# ---------------------------------------------------------------------------
# 18. Retry History Persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_history_persistence():
    async with TestSessionLocal() as session:
        run = WorkflowRun(
            id=uuid.uuid4(),
            workflow_id=uuid.uuid4(),
            workflow_version_id=uuid.uuid4(),
            triggered_by=uuid.uuid4(),
            status="RUNNING",
            input_data={},
        )
        session.add(run)
        await session.commit()

        attempts = 0

        async def flaky_op():
            nonlocal attempts
            attempts += 1
            if attempts <= 2:
                raise httpx.ConnectTimeout(f"Timeout attempt {attempts}")
            return "ok"

        policy = RetryPolicy(max_attempts=3, initial_delay_seconds=0.01)
        mgr = RetryManager(default_policy=policy)

        res = await mgr.execute(
            flaky_op,
            workflow_run_id=run.id,
            operation_name="persist_test",
            db_session=session,
        )
        assert res == "ok"

        # Verify retry attempts recorded in database
        stmt = select(RetryAttempt).where(RetryAttempt.workflow_run_id == run.id).order_by(RetryAttempt.attempt_number)
        db_res = await session.execute(stmt)
        records = list(db_res.scalars().all())

        assert len(records) >= 2
        assert records[0].operation_name == "persist_test"
        assert records[0].error_category == ErrorCategory.NETWORK_TIMEOUT.value


# ---------------------------------------------------------------------------
# 19. Retry History Persistence Failure Does Not Mask Original Error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_history_persistence_failure_does_not_mask_original_error():
    mock_db = MagicMock()
    mock_db.commit = AsyncMock(side_effect=RuntimeError("Database write disk full!"))
    mock_db.rollback = AsyncMock()

    attempts = 0

    async def op():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectTimeout("Network dropped")
        return "done"

    policy = RetryPolicy(max_attempts=3, initial_delay_seconds=0.01)
    mgr = RetryManager(default_policy=policy)

    # Even though mock_db.commit fails, the retry execution succeeds without crashing
    result = await mgr.execute(op, db_session=mock_db, operation_name="db_fail_test")
    assert result == "done"
    assert attempts == 2


# ---------------------------------------------------------------------------
# 20. Document Download Retry Integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_document_download_retry_integration():
    downloader = DocumentDownloader(run_id="test_run", node_id="assessor_node")
    pdf_bytes = b"%PDF-1.4 mock pdf data"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/pdf"}

    async def mock_aiter(chunk_size=8192):
        yield pdf_bytes

    mock_resp.aiter_bytes = mock_aiter

    attempts = 0

    class MockStreamContext:
        async def __aenter__(self):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ReadTimeout("Socket timeout on first download stream")
            return mock_resp

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("app.documents.document_downloader.URLValidator.validate_url", return_value=True), \
         patch("httpx.AsyncClient.stream", return_value=MockStreamContext()):

        result = await downloader.download_direct("https://records.county.gov/sample.pdf")
        assert result.success is True
        assert result.data == pdf_bytes
        assert attempts == 2


# ---------------------------------------------------------------------------
# 21. Browser Navigation Retry Integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_navigation_retry_integration():
    page_mock = MagicMock()
    attempts = 0

    async def mock_goto(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            from playwright.async_api import TimeoutError as PWTimeoutError
            raise PWTimeoutError("Page navigation timeout")
        resp_mock = MagicMock()
        resp_mock.status = 200
        return resp_mock

    page_mock.goto = mock_goto
    page_mock.title = AsyncMock(return_value="County Records")
    page_mock.content = AsyncMock(return_value="<html><body>Clean</body></html>")

    runner = PlaywrightStepRunner(page=page_mock, run_id="run123", node_id="node123")

    steps = [
        {"action": "navigate", "url": "https://records.county.gov/search"}
    ]

    with patch("app.browser.step_runner.URLValidator.validate_url", return_value=True):
        res = await runner.execute_steps(steps, context_vars={})
        assert attempts == 2


# ---------------------------------------------------------------------------
# 22. Database Transient Error Handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_database_transient_error_handling():
    attempts = 0

    async def db_operation():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            # Transient operational error (e.g. server closed connection)
            raise OperationalError("Server closed connection unexpectedly", {}, None)
        return "db_updated"

    mgr = RetryManager()
    res = await mgr.execute(
        db_operation,
        policy=DATABASE_TRANSIENT_POLICY,
        operation_name="transient_db_op",
    )
    assert res == "db_updated"
    assert attempts == 2

    # Verify that non-transient IntegrityError is NOT retried
    int_attempts = 0

    async def integrity_failing_op():
        nonlocal int_attempts
        int_attempts += 1
        raise IntegrityError("Duplicate key violation", {}, None)

    with pytest.raises(IntegrityError):
        await mgr.execute(
            integrity_failing_op,
            policy=DATABASE_TRANSIENT_POLICY,
            operation_name="integrity_op",
        )

    assert int_attempts == 1


# ---------------------------------------------------------------------------
# 23. Sensitive Data Redaction
# ---------------------------------------------------------------------------


def test_sensitive_data_redaction():
    raw_error = (
        "Failed connecting to postgresql://postgres:MySecretPassword123@db.example.com/titlemvp "
        "with Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token123 and api_key=sk-123456789"
    )
    sanitized = sanitize_sensitive_data(raw_error)

    assert "MySecretPassword123" not in sanitized
    assert "token123" not in sanitized
    assert "sk-123456789" not in sanitized
    assert "[REDACTED]" in sanitized

