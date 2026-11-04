"""Comprehensive test suite for Phase 3D.4 Playwright Browser Resilience and Lifecycle Management."""

import asyncio
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.browser.browser_manager import BrowserManager, get_browser_context
from app.browser.step_runner import PlaywrightStepRunner, StepExecutionException
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.main import app
from app.models import (
    BrowserLifecycleState,
    BrowserSessionRecord,
    DocumentArtifact,
    DocumentDownloadAttemptRecord,
    NodeRun,
    RunStatus,
    User,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
)
from app.services.browser_session_service import BrowserSessionService

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
    Workflow.__table__,
    WorkflowVersion.__table__,
    WorkflowRun.__table__,
    NodeRun.__table__,
    DocumentArtifact.__table__,
    DocumentDownloadAttemptRecord.__table__,
    BrowserSessionRecord.__table__,
]


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=ALL_TABLES))
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=ALL_TABLES))


@pytest.fixture
def test_user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest_asyncio.fixture
async def auth_headers(test_user_id: uuid.UUID) -> dict[str, str]:
    async with TestSessionLocal() as session:
        user = User(
            id=test_user_id,
            email=f"test_browser_{uuid.uuid4().hex[:6]}@example.com",
            hashed_password=hash_password("ValidPassword123!"),
            full_name="Browser Test User",
            is_active=True,
            is_superuser=True,
        )
        session.add(user)
        await session.commit()

    token = create_access_token(subject=str(test_user_id))
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_browser_lifecycle_state_transitions():
    """Verifies BrowserManager transitions through INITIALIZING -> READY -> BUSY -> READY -> CLOSED."""
    manager = BrowserManager()
    assert manager.state == BrowserLifecycleState.CLOSED
    assert not manager.is_browser_healthy()

    # Mock playwright launch
    mock_browser = MagicMock()
    mock_browser.is_connected.return_value = True
    mock_browser.on = MagicMock()
    mock_browser.close = AsyncMock()

    mock_context = MagicMock()
    mock_context.set_default_navigation_timeout = MagicMock()
    mock_context.set_default_timeout = MagicMock()
    mock_context.close = AsyncMock()
    mock_context.pages = []

    mock_page = MagicMock()
    mock_page.is_closed.return_value = False
    mock_page.close = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_browser.new_context = AsyncMock(return_value=mock_context)

    mock_playwright = MagicMock()
    mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
    mock_playwright.stop = AsyncMock()

    with patch("app.browser.browser_manager.async_playwright") as mock_ap, \
         patch("app.browser.browser_manager.BrowserSessionService.register_session", new_callable=AsyncMock), \
         patch("app.browser.browser_manager.BrowserSessionService.update_state", new_callable=AsyncMock):

        mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)

        # 1. Get browser -> transitions to READY
        browser = await manager.get_browser()
        assert manager.state == BrowserLifecycleState.READY
        assert manager.is_browser_healthy()

        # 2. Acquire isolated context -> transitions to BUSY
        async with manager.new_isolated_context() as (ctx, pg):
            assert manager.state == BrowserLifecycleState.BUSY
            assert manager.is_context_healthy(ctx)
            assert manager.is_page_healthy(pg)

        # 3. Context exited -> returns to READY
        assert manager.state == BrowserLifecycleState.READY

        # 4. Close manager -> transitions to CLOSED
        await manager.close()
        assert manager.state == BrowserLifecycleState.CLOSED
        assert not manager.is_browser_healthy()


@pytest.mark.asyncio
async def test_browser_health_checks():
    """Tests health verification on browser, context, and page objects."""
    manager = BrowserManager()

    # Browser None or disconnected
    assert not manager.is_browser_healthy()
    assert not manager.is_context_healthy(None)
    assert not manager.is_page_healthy(None)

    mock_browser = MagicMock()
    mock_browser.is_connected.return_value = True
    manager._browser = mock_browser
    manager._state = BrowserLifecycleState.READY

    assert manager.is_browser_healthy()

    # Page closed check
    mock_page = MagicMock()
    mock_page.is_closed.return_value = True
    assert not manager.is_page_healthy(mock_page)

    mock_page.is_closed.return_value = False
    assert manager.is_page_healthy(mock_page)


@pytest.mark.asyncio
async def test_browser_disconnection_event():
    """Tests that Playwright browser disconnection event marks the state as DISCONNECTED."""
    manager = BrowserManager()
    manager._state = BrowserLifecycleState.READY
    manager._current_session_id = "sess_disc_test"

    with patch("app.browser.browser_manager.BrowserSessionService.update_state", new_callable=AsyncMock):
        manager._on_browser_disconnected()
        assert manager.state == BrowserLifecycleState.DISCONNECTED
        assert not manager.is_browser_healthy()
        assert manager._failure_count == 1


@pytest.mark.asyncio
async def test_browser_automatic_crash_recovery():
    """Tests recover_browser cleanly recycles the runtime after an unexpected crash."""
    manager = BrowserManager()
    manager._state = BrowserLifecycleState.CRASHED
    manager._current_session_id = "sess_crashed"

    mock_new_browser = MagicMock()
    mock_new_browser.is_connected.return_value = True
    mock_new_browser.on = MagicMock()

    mock_new_pw = MagicMock()
    mock_new_pw.chromium.launch = AsyncMock(return_value=mock_new_browser)

    with patch("app.browser.browser_manager.async_playwright") as mock_ap, \
         patch("app.browser.browser_manager.BrowserSessionService.update_state", new_callable=AsyncMock), \
         patch("app.browser.browser_manager.BrowserSessionService.register_session", new_callable=AsyncMock):

        mock_ap.return_value.start = AsyncMock(return_value=mock_new_pw)

        recovered = await manager.recover_browser(reason="Simulated SIGSEGV crash")
        assert recovered is mock_new_browser
        assert manager.state == BrowserLifecycleState.READY
        assert manager.is_browser_healthy()


@pytest.mark.asyncio
async def test_step_runner_preflight_page_check():
    """Tests that PlaywrightStepRunner verifies page liveness before executing actions."""
    mock_page = MagicMock()
    mock_page.is_closed.return_value = True  # Closed page

    runner = PlaywrightStepRunner(mock_page, "run-1", "node-1")
    steps = [{"action": "navigate", "url": "https://example.com"}]

    with pytest.raises(StepExecutionException) as exc_info:
        await runner.execute_steps(steps, {})

    assert "Browser page is closed or inoperable" in str(exc_info.value.message)


@pytest.mark.asyncio
async def test_browser_sessions_api_and_persistence(auth_headers: dict[str, str], test_user_id: uuid.UUID):
    """Tests BrowserSessionService persistence and GET /api/v1/executions/{run_id}/browser-sessions."""
    run_id = uuid.uuid4()

    async with TestSessionLocal() as session:
        # Create dummy workflow run
        wf = Workflow(name="Test WF", owner_id=test_user_id)
        session.add(wf)
        await session.flush()

        wfv = WorkflowVersion(workflow_id=wf.id, version_number=1, definition={})
        session.add(wfv)
        await session.flush()

        run = WorkflowRun(
            id=run_id,
            workflow_id=wf.id,
            workflow_version_id=wfv.id,
            status=RunStatus.RUNNING.value,
            triggered_by=test_user_id,
        )
        session.add(run)

        # Create browser session record
        browser_session = BrowserSessionRecord(
            session_id=f"sess_{uuid.uuid4().hex[:10]}",
            workflow_run_id=run_id,
            worker_id="test-worker-1",
            browser_type="chromium",
            state=BrowserLifecycleState.READY.value,
            is_healthy=True,
            pages_created=3,
            contexts_created=1,
            failure_count=0,
        )
        session.add(browser_session)
        await session.commit()

    from app.db.session import get_db
    async def override_get_db():
        async with TestSessionLocal() as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get(f"/api/v1/executions/{run_id}/browser-sessions", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["workflow_run_id"] == str(run_id)
            assert data[0]["browser_type"] == "chromium"
            assert data[0]["pages_created"] == 3
            assert data[0]["state"] == "READY"
    finally:
        app.dependency_overrides.pop(get_db, None)
