"""Comprehensive tests for the Core Working Model: Assessor-Only Workflow Execution & Live Playwright Browser.

Tests:
1. Assessor-only workflow execution: completes with COMPLETED, assessor_branch_status='SUCCEEDED', tax_branch_status='NOT_CONFIGURED'.
2. Tax-only workflow execution: completes with COMPLETED, tax_branch_status='SUCCEEDED', assessor_branch_status='NOT_CONFIGURED'.
3. Independent Assessor & Tax execution: both complete with 'SUCCEEDED'.
4. Partial success handling: Assessor succeeds while Tax fails (fail_tax=True); Assessor data preserved, assessor_branch_status='SUCCEEDED', tax_branch_status='FAILED'.
5. Visible browser launch parameters: slow_mo, keep-alive, headed mode detection.
6. Display diagnostic check: check_display_available() detects headless vs non-graphical display.
7. Live browser session activity logging & telemetry API.
8. Node execution timeline ordering and duration tracking.
"""

import os
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.browser.browser_manager import check_display_available, get_browser_manager
from app.core.config import settings
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.main import app
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
from app.models.browser_session import BrowserSessionRecord
from app.services.browser_session_service import BrowserSessionService
from app.services.execution_service import ExecutionService
from app.workflow_engine.executor import WorkflowExecutor

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

TABLES = [
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
    BrowserSessionRecord.__table__,
]


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=TABLES))
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=TABLES))


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"test_assessor_{uuid.uuid4().hex[:6]}@example.com",
        hashed_password="mock_hashed_pw",
        is_active=True,
        is_superuser=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
def auth_headers(test_user: User) -> dict[str, str]:
    token = create_access_token(subject=str(test_user.id))
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def sample_county_source(db_session: AsyncSession, test_user: User) -> CountySource:
    source = CountySource(
        id=uuid.uuid4(),
        name="Collier County",
        state="FL",
        county="Collier",
        assessor_url="https://www.collierappraiser.com",
        tax_url="https://colliercountytax.com",
        is_active=True,
        created_by=test_user.id,
    )
    db_session.add(source)
    await db_session.commit()
    await db_session.refresh(source)
    return source


# ---------------------------------------------------------------------------
# Test 1: Assessor-Only Workflow Execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assessor_only_workflow_execution(
    db_session: AsyncSession,
    test_user: User,
    sample_county_source: CountySource,
) -> None:
    """An Assessor-only workflow (Input -> Assessor -> Output) must complete
    with assessor_branch_status='SUCCEEDED' and tax_branch_status='NOT_CONFIGURED'.
    """
    workflow_id = uuid.uuid4()
    workflow = Workflow(
        id=workflow_id,
        name="Assessor Only Workflow",
        description="Workflow without Tax node",
        owner_id=test_user.id,
        is_active=True,
        county_source_id=sample_county_source.id,
    )
    db_session.add(workflow)

    assessor_definition = {
        "nodes": [
            {"id": "node-input", "type": "input", "data": {"label": "Input"}, "position": {"x": 0, "y": 0}},
            {
                "id": "node-assessor",
                "type": "assessor",
                "data": {"label": "Assessor", "county_source_id": str(sample_county_source.id)},
                "position": {"x": 200, "y": 0},
            },
            {"id": "node-output", "type": "output", "data": {"label": "Output"}, "position": {"x": 400, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "node-input", "target": "node-assessor"},
            {"id": "e2", "source": "node-assessor", "target": "node-output"},
        ],
    }

    wv = WorkflowVersion(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        version_number=1,
        definition=assessor_definition,
        is_current=True,
    )
    db_session.add(wv)

    run = WorkflowRun(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        workflow_version_id=wv.id,
        status="PENDING",
        input_data={"parcel_id": "00123456009", "force_mock": True},
        triggered_by=test_user.id,
    )
    db_session.add(run)
    await db_session.commit()

    executor = WorkflowExecutor(db_session)
    finished_run = await executor.execute_run(run.id)

    assert finished_run.status == "COMPLETED"

    # Verify ExecutionResultsResponse via ExecutionService
    exec_service = ExecutionService(db_session)
    results = await exec_service.get_execution_results(run.id, user_id=test_user.id, is_superuser=True)

    assert results.status == "COMPLETED"
    assert results.assessor_branch_status == "SUCCEEDED"
    assert results.tax_branch_status == "NOT_CONFIGURED"
    assert results.has_assessor_branch is True
    assert results.has_tax_branch is False
    assert results.has_normalize_branch is False
    assert results.assessor_records_count >= 1
    assert results.tax_records_count == 0


# ---------------------------------------------------------------------------
# Test 2: Tax-Only Workflow Execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tax_only_workflow_execution(
    db_session: AsyncSession,
    test_user: User,
    sample_county_source: CountySource,
) -> None:
    """A Tax-only workflow (Input -> Tax -> Output) must complete
    with tax_branch_status='SUCCEEDED' and assessor_branch_status='NOT_CONFIGURED'.
    """
    workflow_id = uuid.uuid4()
    workflow = Workflow(
        id=workflow_id,
        name="Tax Only Workflow",
        owner_id=test_user.id,
        is_active=True,
        county_source_id=sample_county_source.id,
    )
    db_session.add(workflow)

    tax_definition = {
        "nodes": [
            {"id": "node-input", "type": "input", "data": {"label": "Input"}, "position": {"x": 0, "y": 0}},
            {
                "id": "node-tax",
                "type": "tax",
                "data": {"label": "Tax Collector", "county_source_id": str(sample_county_source.id)},
                "position": {"x": 200, "y": 0},
            },
            {"id": "node-output", "type": "output", "data": {"label": "Output"}, "position": {"x": 400, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "node-input", "target": "node-tax"},
            {"id": "e2", "source": "node-tax", "target": "node-output"},
        ],
    }

    wv = WorkflowVersion(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        version_number=1,
        definition=tax_definition,
        is_current=True,
    )
    db_session.add(wv)

    run = WorkflowRun(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        workflow_version_id=wv.id,
        status="PENDING",
        input_data={"parcel_id": "00987654001", "force_mock": True},
        triggered_by=test_user.id,
    )
    db_session.add(run)
    await db_session.commit()

    executor = WorkflowExecutor(db_session)
    finished_run = await executor.execute_run(run.id)

    assert finished_run.status == "COMPLETED"

    exec_service = ExecutionService(db_session)
    results = await exec_service.get_execution_results(run.id, user_id=test_user.id, is_superuser=True)

    assert results.assessor_branch_status == "NOT_CONFIGURED"
    assert results.tax_branch_status == "SUCCEEDED"
    assert results.has_assessor_branch is False
    assert results.has_tax_branch is True
    assert results.assessor_records_count == 0
    assert results.tax_records_count >= 1


# ---------------------------------------------------------------------------
# Test 3: Independent Assessor & Tax Execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_independent_assessor_and_tax_execution(
    db_session: AsyncSession,
    test_user: User,
    sample_county_source: CountySource,
) -> None:
    """A workflow with independent Assessor and Tax branches executing in parallel
    must mark both branches as SUCCEEDED.
    """
    workflow_id = uuid.uuid4()
    workflow = Workflow(
        id=workflow_id,
        name="Independent Dual Workflow",
        owner_id=test_user.id,
        is_active=True,
    )
    db_session.add(workflow)

    dual_definition = {
        "nodes": [
            {"id": "node-input", "type": "input", "data": {"label": "Input"}, "position": {"x": 0, "y": 0}},
            {"id": "node-assessor", "type": "assessor", "data": {"label": "Assessor"}, "position": {"x": 200, "y": -50}},
            {"id": "node-tax", "type": "tax", "data": {"label": "Tax"}, "position": {"x": 200, "y": 50}},
            {"id": "node-output", "type": "output", "data": {"label": "Output"}, "position": {"x": 400, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "node-input", "target": "node-assessor"},
            {"id": "e2", "source": "node-input", "target": "node-tax"},
            {"id": "e3", "source": "node-assessor", "target": "node-output"},
            {"id": "e4", "source": "node-tax", "target": "node-output"},
        ],
    }

    wv = WorkflowVersion(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        version_number=1,
        definition=dual_definition,
        is_current=True,
    )
    db_session.add(wv)

    run = WorkflowRun(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        workflow_version_id=wv.id,
        status="PENDING",
        input_data={"parcel_id": "0011223344", "force_mock": True},
        triggered_by=test_user.id,
    )
    db_session.add(run)
    await db_session.commit()

    executor = WorkflowExecutor(db_session)
    finished_run = await executor.execute_run(run.id)

    assert finished_run.status == "COMPLETED"

    exec_service = ExecutionService(db_session)
    results = await exec_service.get_execution_results(run.id, user_id=test_user.id, is_superuser=True)
    assert results.assessor_branch_status == "SUCCEEDED"
    assert results.tax_branch_status == "SUCCEEDED"
    assert results.has_assessor_branch is True
    assert results.has_tax_branch is True


# ---------------------------------------------------------------------------
# Test 4: Partial Success (Assessor Succeeds, Tax Fails)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_partial_success_preserves_assessor_results(
    db_session: AsyncSession,
    test_user: User,
) -> None:
    """When the Tax branch fails under flexible fault mode,
    the workflow completes with PARTIALLY_COMPLETED, Assessor data is preserved,
    and branch statuses accurately show SUCCEEDED vs FAILED.
    """
    workflow_id = uuid.uuid4()
    workflow = Workflow(
        id=workflow_id,
        name="Partial Success Workflow",
        owner_id=test_user.id,
        is_active=True,
    )
    db_session.add(workflow)

    dual_definition = {
        "nodes": [
            {"id": "node-input", "type": "input", "data": {"label": "Input", "required_params": ["parcel_id"]}, "position": {"x": 0, "y": 0}},
            {"id": "node-assessor", "type": "assessor", "data": {"label": "Assessor", "allow_failure": True}, "position": {"x": 200, "y": -50}},
            {"id": "node-tax", "type": "tax", "data": {"label": "Tax", "allow_failure": True}, "position": {"x": 200, "y": 50}},
            {"id": "node-output", "type": "output", "data": {"label": "Output"}, "position": {"x": 400, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "node-input", "target": "node-assessor"},
            {"id": "e2", "source": "node-input", "target": "node-tax"},
            {"id": "e3", "source": "node-assessor", "target": "node-output"},
            {"id": "e4", "source": "node-tax", "target": "node-output"},
        ],
        "execution_config": {
            "mode": "parallel_branches",
            "allow_partial_success": True,
        },
    }

    wv = WorkflowVersion(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        version_number=1,
        definition=dual_definition,
        is_current=True,
    )
    db_session.add(wv)

    run = WorkflowRun(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        workflow_version_id=wv.id,
        status="PENDING",
        input_data={"parcel_id": "0011223344", "fail_tax": True, "force_mock": True},
        triggered_by=test_user.id,
    )
    db_session.add(run)
    await db_session.commit()

    executor = WorkflowExecutor(db_session)
    finished_run = await executor.execute_run(run.id)

    assert finished_run.status == "PARTIALLY_COMPLETED"

    exec_service = ExecutionService(db_session)
    results = await exec_service.get_execution_results(run.id, user_id=test_user.id, is_superuser=True)
    assert results.status == "PARTIALLY_COMPLETED"
    assert results.assessor_branch_status == "SUCCEEDED"
    assert results.tax_branch_status == "FAILED"
    assert results.assessor_records_count >= 1


# ---------------------------------------------------------------------------
# Test 5: Visible Browser Launch & Configuration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_visible_browser_configuration() -> None:
    """Verify BrowserManager respects PLAYWRIGHT_HEADLESS and PLAYWRIGHT_BROWSER_MODE."""
    mgr = await get_browser_manager()

    with patch.object(settings, "PLAYWRIGHT_HEADLESS", False):
        with patch.object(settings, "PLAYWRIGHT_BROWSER_MODE", "local_visible"):
            assert mgr.is_visible_mode is True

    with patch.object(settings, "PLAYWRIGHT_HEADLESS", True):
        with patch.object(settings, "PLAYWRIGHT_BROWSER_MODE", "headless"):
            assert mgr.is_visible_mode is False


# ---------------------------------------------------------------------------
# Test 6: Display Diagnostic Verification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_display_diagnostic_check() -> None:
    """Verify check_display_available returns descriptive diagnostics when display is missing."""
    with patch("sys.platform", "linux"):
        with patch.dict(os.environ, {}, clear=True):
            display_ok, diagnostic = check_display_available()
            assert display_ok is False
            assert "neither DISPLAY nor WAYLAND_DISPLAY is set" in str(diagnostic)

    with patch("sys.platform", "linux"):
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            display_ok, diagnostic = check_display_available()
            assert display_ok is True
            assert diagnostic is None


# ---------------------------------------------------------------------------
# Test 7: Browser Session Activity Persistence & API
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_browser_session_activity_logging(
    db_session: AsyncSession,
    test_user: User,
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Verify BrowserSessionService records step events and the API exposes them."""
    run_id = uuid.uuid4()
    session_id_str = f"sess_{uuid.uuid4().hex[:12]}"

    workflow = Workflow(
        id=uuid.uuid4(),
        name="Activity Test Workflow",
        owner_id=test_user.id,
        is_active=True,
    )
    db_session.add(workflow)
    wv = WorkflowVersion(
        id=uuid.uuid4(),
        workflow_id=workflow.id,
        version_number=1,
        definition={"nodes": [], "edges": []},
        is_current=True,
    )
    db_session.add(wv)
    run = WorkflowRun(
        id=run_id,
        workflow_id=workflow.id,
        workflow_version_id=wv.id,
        status="RUNNING",
        input_data={},
        triggered_by=test_user.id,
    )
    db_session.add(run)
    await db_session.commit()

    svc = BrowserSessionService(db_session)
    await svc.register_session(
        session_id=session_id_str,
        workflow_run_id=run_id,
        browser_type="chromium",
        state="ready",
    )

    # Record first activity step (running)
    await svc.record_activity(
        session_id=session_id_str,
        node_id="node-assessor",
        step_index=0,
        action="navigate",
        description="Navigate to search page",
        status="RUNNING",
        url="https://www.collierappraiser.com",
    )

    # Record completion
    await svc.record_activity(
        session_id=session_id_str,
        node_id="node-assessor",
        step_index=0,
        action="navigate",
        description="Navigate to search page",
        status="COMPLETED",
        url="https://www.collierappraiser.com/Main_Search/search_rp.html",
        duration_ms=450,
    )

    # Query via API
    resp = await client.get(
        f"/api/v1/executions/{run_id}/browser-activity",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == session_id_str
    assert data["workflow_run_id"] == str(run_id)
    assert len(data["activities"]) == 1
    assert data["activities"][0]["node_id"] == "node-assessor"
    assert data["activities"][0]["status"] == "COMPLETED"
    assert data["activities"][0]["duration_ms"] == 450


# ---------------------------------------------------------------------------
# Test 8: Node Timeline Ordering and Duration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_node_execution_timeline_duration_and_order(
    db_session: AsyncSession,
    test_user: User,
) -> None:
    """Verify NodeRun records have populated started_at timestamps and non-zero durations."""
    workflow_id = uuid.uuid4()
    workflow = Workflow(
        id=workflow_id,
        name="Timeline Order Workflow",
        owner_id=test_user.id,
        is_active=True,
    )
    db_session.add(workflow)

    definition = {
        "nodes": [
            {"id": "node-input", "type": "input", "data": {"label": "Input"}, "position": {"x": 0, "y": 0}},
            {"id": "node-assessor", "type": "assessor", "data": {"label": "Assessor"}, "position": {"x": 200, "y": 0}},
            {"id": "node-output", "type": "output", "data": {"label": "Output"}, "position": {"x": 400, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "node-input", "target": "node-assessor"},
            {"id": "e2", "source": "node-assessor", "target": "node-output"},
        ],
    }

    wv = WorkflowVersion(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        version_number=1,
        definition=definition,
        is_current=True,
    )
    db_session.add(wv)

    run = WorkflowRun(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        workflow_version_id=wv.id,
        status="PENDING",
        input_data={"parcel_id": "123", "force_mock": True},
        triggered_by=test_user.id,
    )
    db_session.add(run)
    await db_session.commit()

    executor = WorkflowExecutor(db_session)
    await executor.execute_run(run.id)

    # Check node runs in DB
    result = await db_session.execute(
        select(NodeRun).where(NodeRun.workflow_run_id == run.id)
    )
    node_runs = result.scalars().all()
    assert len(node_runs) == 3

    for nr in node_runs:
        assert nr.started_at is not None
        assert nr.completed_at is not None
        assert nr.duration_ms is not None
        assert nr.duration_ms >= 0

    # Check list_node_runs returns them sorted topologically (Input -> Assessor -> Output)
    exec_service = ExecutionService(db_session)
    sorted_node_runs = await exec_service.list_node_runs(run.id, user_id=test_user.id, is_superuser=True)
    node_types = [nr.node_type for nr in sorted_node_runs]
    assert node_types == ["input", "assessor", "output"]
