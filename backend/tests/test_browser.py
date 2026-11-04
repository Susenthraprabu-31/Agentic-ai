"""Unit and integration tests for Phase 3A Playwright Automation Engine.

Tests:
1. BrowserManager singleton lifecycle and isolated contexts.
2. SecurityDetector anti-bot and challenge detection (Cloudflare, Turnstile, reCAPTCHA, hCaptcha, Rate Limits).
3. EvidenceCollector screenshot and DOM dump generation.
4. PlaywrightStepRunner step execution, variable substitution, and security halt.
5. AssessorNodeExecutor and TaxNodeExecutor browser execution and mock fixture fallback.
6. WorkflowExecutor persistence of SecurityEvent and evidence tracking.
"""

import asyncio
import os
import tempfile
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.browser.browser_manager import BrowserManager, get_browser_context, get_browser_manager
from app.browser.evidence_collector import EvidenceCollector
from app.browser.security_detector import SecurityDetector
from app.browser.step_runner import (
    PlaywrightStepRunner,
    SecurityChallengeException,
    StepExecutionException,
)
from app.core.config import settings
from app.db.base import Base
from app.models import (
    AssessorResult,
    CountySource,
    NodeRun,
    Recipe,
    RecipeVersion,
    RefreshToken,
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
from app.workflow_engine.execution_context import ExecutionContext
from app.workflow_engine.executor import WorkflowExecutor
from app.workflow_engine.node_executor import AssessorNodeExecutor, TaxNodeExecutor

# ---------------------------------------------------------------------------
# Test DB Setup
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

BROWSER_TEST_TABLES = [
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
]


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(sync_conn, tables=BROWSER_TEST_TABLES)
        )
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=BROWSER_TEST_TABLES)
        )


# ---------------------------------------------------------------------------
# 1. SecurityDetector Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_security_detector_clean_page():
    """Verify normal page returns is_detected=False."""
    async with get_browser_context() as (_, page):
        html = "<html><head><title>County Property Search</title></head><body><h1>Welcome</h1></body></html>"
        await page.set_content(html)
        res = await SecurityDetector.inspect_page(page)
        assert not res.is_detected


@pytest.mark.asyncio
async def test_security_detector_cloudflare_title():
    """Detect Cloudflare challenge by title."""
    async with get_browser_context() as (_, page):
        html = "<html><head><title>Just a moment...</title></head><body>Verifying your browser</body></html>"
        await page.set_content(html)
        res = await SecurityDetector.inspect_page(page)
        assert res.is_detected
        assert res.event_type == "cloudflare"
        assert "just a moment" in (res.reason or "").lower()


@pytest.mark.asyncio
async def test_security_detector_turnstile_dom():
    """Detect Cloudflare Turnstile element in DOM."""
    async with get_browser_context() as (_, page):
        html = '<html><body><h1>Search Portal</h1><div class="cf-turnstile"></div></body></html>'
        await page.set_content(html)
        res = await SecurityDetector.inspect_page(page)
        assert res.is_detected
        assert res.event_type == "turnstile"


@pytest.mark.asyncio
async def test_security_detector_recaptcha_dom():
    """Detect Google reCAPTCHA widget in DOM."""
    async with get_browser_context() as (_, page):
        html = '<html><body><h1>Search</h1><div class="g-recaptcha" data-sitekey="test"></div></body></html>'
        await page.set_content(html)
        res = await SecurityDetector.inspect_page(page)
        assert res.is_detected
        assert res.event_type == "captcha"
        assert "CAPTCHA" in (res.reason or "")


@pytest.mark.asyncio
async def test_security_detector_rate_limit_response():
    """Detect HTTP 429 status response barrier."""
    from unittest.mock import MagicMock
    mock_resp = MagicMock(status=429, headers={"retry-after": "60"})
    res = await SecurityDetector.inspect_response(mock_resp, url="https://county.gov/search")
    assert res is not None
    assert res.is_detected
    assert res.event_type == "rate_limit"


# ---------------------------------------------------------------------------
# 2. EvidenceCollector Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evidence_collector_capture():
    """Verify evidence collector writes screenshot and HTML dump."""
    async with get_browser_context() as (_, page):
        html = "<html><head><title>Evidence Test</title></head><body><div id='content'>Sample County Page</div></body></html>"
        await page.set_content(html)

        run_id = f"test-run-{uuid.uuid4().hex[:8]}"
        evidence = await EvidenceCollector.capture_evidence(page, run_id=run_id, node_id="test_step")

        assert evidence["screenshot_path"] is not None
        assert evidence["html_path"] is not None
        assert os.path.exists(evidence["screenshot_path"])
        assert os.path.exists(evidence["html_path"])

        with open(evidence["html_path"], "r", encoding="utf-8") as f:
            saved_html = f.read()
        assert "Sample County Page" in saved_html


# ---------------------------------------------------------------------------
# 3. BrowserManager Isolated Contexts Test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_browser_manager_isolated_contexts():
    """Verify two concurrent contexts do not share cookies or state."""
    mgr = await get_browser_manager()

    async with mgr.new_isolated_context() as (ctx1, page1):
        async with mgr.new_isolated_context() as (ctx2, page2):
            await ctx1.add_cookies([{"name": "session_user", "value": "alice", "domain": "example.com", "path": "/"}])
            cookies1 = await ctx1.cookies("https://example.com")
            cookies2 = await ctx2.cookies("https://example.com")

            assert len(cookies1) == 1
            assert cookies1[0]["value"] == "alice"
            assert len(cookies2) == 0  # Context 2 is completely clean & isolated


# ---------------------------------------------------------------------------
# 4. PlaywrightStepRunner Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_step_runner_success_flow():
    """Execute declarative steps on a synthetic form page with variable substitution."""
    async with get_browser_context() as (_, page):
        # Serve synthetic form via data URL
        form_html = (
            "<html><body>"
            "<form id='search-form'>"
            "<input id='parcel-input' type='text' />"
            "<button id='search-btn' type='button' onclick=\"document.getElementById('result').innerText = 'Owner: John Doe';\">Search</button>"
            "<div id='result'></div>"
            "</form>"
            "</body></html>"
        )
        await page.set_content(form_html)

        steps = [
            {"action": "fill", "selector": "#parcel-input", "value": "{{input.parcel_id}}"},
            {"action": "click", "selector": "#search-btn"},
            {"action": "wait", "timeout_ms": 100},
        ]
        context_vars = {"input": {"parcel_id": "123-456-789"}}
        extract_schema = {
            "result_text": {"selector": "#result", "attribute": "text"},
            "input_val": {"selector": "#parcel-input", "attribute": "value"},
        }

        runner = PlaywrightStepRunner(page, run_id="test-run-1", node_id="node-assessor")
        extracted = await runner.execute_steps(steps, context_vars, extract_schema)

        assert extracted["result_text"] == "Owner: John Doe"
        assert extracted["input_val"] == "123-456-789"


@pytest.mark.asyncio
async def test_step_runner_security_challenge_halt():
    """Verify that encountering a security challenge immediately halts and raises SecurityChallengeException."""
    async with get_browser_context() as (_, page):
        challenge_html = (
            "<html><head><title>Attention Required! | Cloudflare</title></head>"
            "<body><div class='cf-turnstile'></div></body></html>"
        )
        await page.set_content(challenge_html)

        steps = [
            {"action": "click", "selector": "#search-btn"},
        ]
        runner = PlaywrightStepRunner(page, run_id="test-run-sec", node_id="node-assessor")

        with pytest.raises(SecurityChallengeException) as exc_info:
            await runner.execute_steps(steps, {})

        assert exc_info.value.result.is_detected
        assert exc_info.value.result.event_type in ("cloudflare", "turnstile")


# ---------------------------------------------------------------------------
# 5. NodeExecutor Integration Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assessor_executor_mock_fallback():
    """Verify AssessorNodeExecutor falls back to mock fixture when recipe_steps is omitted."""
    context = ExecutionContext(
        run_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        workflow_definition={},
        initial_input={"parcel_id": "99-88-77"},
    )
    executor = AssessorNodeExecutor()
    res = await executor.execute(node={"id": "node-assessor", "data": {}}, context=context)

    assert res.status == "COMPLETED"
    assert res.output_data["assessor"]["parcel_number"] == "99-88-77"
    assert res.output_data["assessor"]["_mock"] is True


@pytest.mark.asyncio
async def test_assessor_executor_security_challenge_handling():
    """Verify AssessorNodeExecutor catches security challenge, emits security event & evidence, and fails safely."""
    context = ExecutionContext(
        run_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        workflow_definition={},
        initial_input={"parcel_id": "11-22-33"},
    )

    # Recipe steps that navigate to a page with Cloudflare challenge
    node_def = {
        "id": "node-assessor",
        "data": {
            "recipe_steps": [
                {
                    "action": "navigate",
                    "url": "data:text/html,<html><head><title>Just a moment...</title></head><body>Checking browser</body></html>",
                }
            ]
        },
    }

    executor = AssessorNodeExecutor()
    res = await executor.execute(node=node_def, context=context)

    assert res.status == "FAILED"
    assert "Security Challenge Detected" in res.error_message
    assert res.security_event is not None
    assert res.security_event["event_type"] == "cloudflare"
    assert res.evidence is not None


# ---------------------------------------------------------------------------
# 6. WorkflowExecutor Persistence of Security Events
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workflow_execution_persists_security_event():
    """Verify end-to-end execution of a workflow run that encounters a security barrier creates a SecurityEvent DB record."""
    user_id = uuid.uuid4()
    wf_id = uuid.uuid4()
    ver_id = uuid.uuid4()
    run_id = uuid.uuid4()

    workflow_def = {
        "nodes": [
            {
                "id": "node-input",
                "type": "input",
                "data": {"required_fields": ["parcel_id"]},
            },
            {
                "id": "node-assessor",
                "type": "assessor",
                "data": {
                    "recipe_steps": [
                        {
                            "action": "navigate",
                            "url": "data:text/html,<html><head><title>Just a moment...</title></head><body>Anti-bot</body></html>",
                        }
                    ]
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "node-input", "target": "node-assessor"},
        ],
    }

    async with TestSessionLocal() as session:
        user = User(id=user_id, email="sec_test@example.com", hashed_password="pw", is_active=True)
        session.add(user)
        wf = Workflow(id=wf_id, name="Security Workflow", owner_id=user_id)
        session.add(wf)
        wf_ver = WorkflowVersion(
            id=ver_id,
            workflow_id=wf_id,
            version_number=1,
            definition=workflow_def,
            is_current=True,
        )
        session.add(wf_ver)
        wf_run = WorkflowRun(
            id=run_id,
            workflow_id=wf_id,
            workflow_version_id=ver_id,
            status="QUEUED",
            input_data={"parcel_id": "SEC-001"},
            triggered_by=user_id,
        )
        session.add(wf_run)
        await session.commit()

    async with TestSessionLocal() as session:
        executor = WorkflowExecutor(session)
        final_run = await executor.execute_run(run_id)

        assert final_run.status == "FAILED"
        assert "Security Challenge Detected" in (final_run.error_message or "")

        # Verify SecurityEvent was persisted to the database
        sec_stmt = select(SecurityEvent).where(SecurityEvent.workflow_run_id == run_id)
        sec_res = await session.execute(sec_stmt)
        sec_event = sec_res.scalar_one_or_none()

        assert sec_event is not None
        assert sec_event.event_type == "cloudflare"
        assert "just a moment" in str(sec_event.details).lower()
