"""Unit and integration tests for Phase 2E Celery Background Execution and Workflow Engine."""

import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
from app.models.security_event import SecurityEvent
from app.workflow_engine.execution_context import ExecutionContext
from app.workflow_engine.executor import WorkflowExecutor
from app.workflow_engine.scheduler import WorkflowScheduler

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

EXECUTION_TABLES = [
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
            lambda sync_conn: Base.metadata.create_all(sync_conn, tables=EXECUTION_TABLES)
        )
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=EXECUTION_TABLES)
        )


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user() -> User:
    async with TestSessionLocal() as session:
        user = User(
            id=uuid.uuid4(),
            email="exec_tester@example.com",
            hashed_password="hashed_pw_test",
            full_name="Execution Tester",
            is_active=True,
            is_superuser=False,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def other_user() -> User:
    async with TestSessionLocal() as session:
        user = User(
            id=uuid.uuid4(),
            email="other_exec_user@example.com",
            hashed_password="hashed_pw_test",
            full_name="Other User",
            is_active=True,
            is_superuser=False,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def make_auth_headers(user: User) -> dict[str, str]:
    token = create_access_token(subject=str(user.id))
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Test Graphs
# ---------------------------------------------------------------------------

DUAL_BRANCH_WORKFLOW_DEF = {
    "nodes": [
        {
            "id": "node-input",
            "type": "input",
            "position": {"x": 100, "y": 200},
            "data": {"required_params": ["parcel_id"]},
        },
        {
            "id": "node-assessor",
            "type": "assessor",
            "position": {"x": 400, "y": 100},
            "data": {"allow_failure": True},
        },
        {
            "id": "node-tax",
            "type": "tax",
            "position": {"x": 400, "y": 300},
            "data": {"allow_failure": True},
        },
        {
            "id": "node-normalize",
            "type": "normalize",
            "position": {"x": 700, "y": 200},
            "data": {"support_partial_success": True},
        },
        {
            "id": "node-output",
            "type": "output",
            "position": {"x": 950, "y": 200},
            "data": {"output_format": "json"},
        },
    ],
    "edges": [
        {"id": "e1", "source": "node-input", "target": "node-assessor"},
        {"id": "e2", "source": "node-input", "target": "node-tax"},
        {"id": "e3", "source": "node-assessor", "target": "node-normalize"},
        {"id": "e4", "source": "node-tax", "target": "node-normalize"},
        {"id": "e5", "source": "node-normalize", "target": "node-output"},
    ],
    "execution_config": {
        "mode": "parallel_branches",
        "allow_partial_success": True,
        "timeout_seconds": 120,
    },
}

# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_full_dual_branch_execution(test_user: User):
    """Test full dual-branch execution (Assessor + Tax) using the WorkflowExecutor engine."""
    async with TestSessionLocal() as session:
        wf = Workflow(name="Dual Branch Test Workflow", owner_id=test_user.id)
        session.add(wf)
        await session.flush()

        version = WorkflowVersion(
            workflow_id=wf.id,
            version_number=1,
            definition=DUAL_BRANCH_WORKFLOW_DEF,
            is_current=True,
        )
        session.add(version)
        await session.flush()

        run = WorkflowRun(
            workflow_id=wf.id,
            workflow_version_id=version.id,
            triggered_by=test_user.id,
            status="PENDING",
            input_data={"parcel_id": "01-002-0034", "address": "123 Main St"},
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    # Execute workflow run
    async with TestSessionLocal() as session:
        executor = WorkflowExecutor(session)
        finished_run = await executor.execute_run(run_id)

        assert finished_run.status == "COMPLETED"
        assert finished_run.duration_ms is not None
        assert finished_run.duration_ms >= 0
        assert len(finished_run.node_runs) == 5

        # Verify NodeRun statuses
        for nr in finished_run.node_runs:
            assert nr.status == "COMPLETED"

        # Check metadata
        meta = finished_run.execution_metadata
        assert meta["assessor_success"] is True
        assert meta["tax_success"] is True

        # Verify AssessorResult row
        assessor_res = (
            await session.execute(
                AssessorResult.__table__.select().where(
                    AssessorResult.parcel_number == "01-002-0034"
                )
            )
        ).first()
        assert assessor_res is not None
        assert assessor_res.assessed_value == 285000.00

        # Verify TaxResult row
        tax_res = (
            await session.execute(
                TaxResult.__table__.select().where(TaxResult.parcel_number == "01-002-0034")
            )
        ).first()
        assert tax_res is not None
        assert tax_res.status == "PAID"


@pytest.mark.asyncio
async def test_engine_partial_success_when_tax_fails(test_user: User):
    """Test fault-tolerant partial completion when Tax fails and Assessor succeeds."""
    async with TestSessionLocal() as session:
        wf = Workflow(name="Fault Tolerant Workflow", owner_id=test_user.id)
        session.add(wf)
        await session.flush()

        version = WorkflowVersion(
            workflow_id=wf.id,
            version_number=1,
            definition=DUAL_BRANCH_WORKFLOW_DEF,
            is_current=True,
        )
        session.add(version)
        await session.flush()

        # Trigger tax branch failure via input parameter
        run = WorkflowRun(
            workflow_id=wf.id,
            workflow_version_id=version.id,
            triggered_by=test_user.id,
            status="PENDING",
            input_data={"parcel_id": "01-002-0034", "fail_tax": True},
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    async with TestSessionLocal() as session:
        executor = WorkflowExecutor(session)
        finished_run = await executor.execute_run(run_id)

        # Status must be PARTIALLY_COMPLETED, not FAILED
        assert finished_run.status == "PARTIALLY_COMPLETED"
        node_status_map = {nr.node_type: nr.status for nr in finished_run.node_runs}
        assert node_status_map["assessor"] == "COMPLETED"
        assert node_status_map["tax"] == "FAILED"
        assert node_status_map["normalize"] == "COMPLETED"
        assert node_status_map["output"] == "COMPLETED"

        # Verify normalized property data preserved assessor info and recorded missing tax source
        norm_run = next(nr for nr in finished_run.node_runs if nr.node_type == "normalize")
        norm_data = norm_run.output_data["normalized"]
        assert norm_data["is_partial"] is True
        assert "tax" in norm_data["missing_sources"]
        assert norm_data["total_assessed_value"] == 285000.00


@pytest.mark.asyncio
async def test_engine_cancellation_handling(test_user: User):
    """Test that setting CANCEL_REQUESTED stops subsequent nodes and marks run CANCELLED."""
    async with TestSessionLocal() as session:
        wf = Workflow(name="Cancelled Workflow", owner_id=test_user.id)
        session.add(wf)
        await session.flush()

        version = WorkflowVersion(
            workflow_id=wf.id,
            version_number=1,
            definition=DUAL_BRANCH_WORKFLOW_DEF,
            is_current=True,
        )
        session.add(version)
        await session.flush()

        # Mark run as CANCEL_REQUESTED prior to execution
        run = WorkflowRun(
            workflow_id=wf.id,
            workflow_version_id=version.id,
            triggered_by=test_user.id,
            status="CANCEL_REQUESTED",
            input_data={"parcel_id": "01-002-0034"},
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    async with TestSessionLocal() as session:
        executor = WorkflowExecutor(session)
        cancelled_run = await executor.execute_run(run_id)

        assert cancelled_run.status == "CANCELLED"
        for nr in cancelled_run.node_runs:
            assert nr.status == "CANCELLED"


@pytest.mark.asyncio
async def test_api_workflow_execute_and_query_endpoints(
    async_client: AsyncClient, test_user: User, other_user: User
):
    """Test POST /workflows/{id}/execute, GET /executions, GET /results, and ownership security."""
    headers = make_auth_headers(test_user)
    other_headers = make_auth_headers(other_user)

    # 1. Create Workflow
    wf_resp = await async_client.post(
        "/api/v1/workflows",
        json={
            "name": "API Execution Test Workflow",
            "initial_definition": DUAL_BRANCH_WORKFLOW_DEF,
        },
        headers=headers,
    )
    assert wf_resp.status_code == 201
    wf_id = wf_resp.json()["id"]

    # 2. Submit Execution -> 202 Accepted
    exec_resp = await async_client.post(
        f"/api/v1/workflows/{wf_id}/execute",
        json={"input_data": {"parcel_id": "01-002-0034"}},
        headers=headers,
    )
    assert exec_resp.status_code == 202, exec_resp.text
    run_data = exec_resp.json()
    run_id = run_data["id"]
    assert run_data["status"] in ("PENDING", "QUEUED")

    # 3. List Executions
    list_resp = await async_client.get("/api/v1/executions", headers=headers)
    assert list_resp.status_code == 200
    runs_list = list_resp.json()
    assert runs_list["total"] >= 1
    assert any(r["id"] == run_id for r in runs_list["items"])

    # 4. Get Execution Details
    detail_resp = await async_client.get(f"/api/v1/executions/{run_id}", headers=headers)
    assert detail_resp.status_code == 200
    assert detail_resp.json()["id"] == run_id

    # 5. Cancel Execution
    cancel_resp = await async_client.post(f"/api/v1/executions/{run_id}/cancel", headers=headers)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "CANCEL_REQUESTED"

    # 6. Security Authorization Check: Other user cannot access run
    sec_get = await async_client.get(f"/api/v1/executions/{run_id}", headers=other_headers)
    assert sec_get.status_code == 403

    sec_cancel = await async_client.post(f"/api/v1/executions/{run_id}/cancel", headers=other_headers)
    assert sec_cancel.status_code == 403

