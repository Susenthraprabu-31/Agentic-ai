"""Unit and integration tests for Phase 2D Visual Workflow Builder."""

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
    CountySource,
    Recipe,
    RecipeVersion,
    RefreshToken,
    SourceConfiguration,
    TemplateVersion,
    UITemplate,
    User,
    ValidationCase,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
)
from app.services.workflow_validator import validate_workflow_graph

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

WORKFLOW_TABLES = [
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
]


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(sync_conn, tables=WORKFLOW_TABLES)
        )
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=WORKFLOW_TABLES)
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
            email="builder_tester@example.com",
            hashed_password="hashed_pw_test",
            full_name="Workflow Tester",
            is_active=True,
            is_superuser=False,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def second_user() -> User:
    async with TestSessionLocal() as session:
        user = User(
            id=uuid.uuid4(),
            email="other_builder@example.com",
            hashed_password="hashed_pw_test",
            full_name="Other Tester",
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
# Test Cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_graph_validator_dag_and_independent_branches():
    """Test pure graph validator for DAG, cycles, independent Assessor/Tax execution, and partial success."""
    # 1. Valid Dual Branch (Assessor + Tax + Normalize + Partial Success)
    valid_dual = {
        "nodes": [
            {"id": "n1", "type": "input", "data": {"required_params": ["parcel_id"]}},
            {"id": "n2", "type": "assessor", "data": {"allow_failure": True}},
            {"id": "n3", "type": "tax", "data": {"allow_failure": True}},
            {"id": "n4", "type": "normalize", "data": {"support_partial_success": True}},
            {"id": "n5", "type": "output", "data": {"output_format": "json"}},
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n1", "target": "n3"},
            {"source": "n2", "target": "n4"},
            {"source": "n3", "target": "n4"},
            {"source": "n4", "target": "n5"},
        ],
    }
    val = validate_workflow_graph(valid_dual)
    assert val["is_valid"] is True
    assert val["supports_assessor"] is True
    assert val["supports_tax"] is True
    assert val["supports_partial_success"] is True
    assert len(val["execution_order"]) == 5

    # 2. Valid Independent Assessor Only Branch
    valid_assessor_only = {
        "nodes": [
            {"id": "n1", "type": "input"},
            {"id": "n2", "type": "assessor"},
            {"id": "n3", "type": "output"},
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
        ],
    }
    val_assessor = validate_workflow_graph(valid_assessor_only)
    assert val_assessor["is_valid"] is True
    assert val_assessor["supports_assessor"] is True
    assert val_assessor["supports_tax"] is False

    # 3. Valid Independent Tax Only Branch
    valid_tax_only = {
        "nodes": [
            {"id": "n1", "type": "input"},
            {"id": "n2", "type": "tax"},
            {"id": "n3", "type": "output"},
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
        ],
    }
    val_tax = validate_workflow_graph(valid_tax_only)
    assert val_tax["is_valid"] is True
    assert val_tax["supports_tax"] is True
    assert val_tax["supports_assessor"] is False

    # 4. Cycle Detection Failure
    cyclic_graph = {
        "nodes": [
            {"id": "n1", "type": "input"},
            {"id": "n2", "type": "assessor"},
            {"id": "n3", "type": "normalize"},
            {"id": "n4", "type": "output"},
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
            {"source": "n3", "target": "n2"},  # Cycle: n2 -> n3 -> n2
            {"source": "n3", "target": "n4"},
        ],
    }
    val_cycle = validate_workflow_graph(cyclic_graph)
    assert val_cycle["is_valid"] is False
    assert any("Cycle detected" in err for err in val_cycle["errors"])

    # 5. Invalid Node Type Failure
    invalid_node = {
        "nodes": [
            {"id": "n1", "type": "input"},
            {"id": "n2", "type": "arbitrary_script"},
            {"id": "n3", "type": "output"},
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
        ],
    }
    val_invalid = validate_workflow_graph(invalid_node)
    assert val_invalid["is_valid"] is False
    assert any("unsupported type 'arbitrary_script'" in err for err in val_invalid["errors"])


@pytest.mark.asyncio
async def test_workflow_crud_and_versioning(async_client: AsyncClient, test_user: User):
    """Test full workflow CRUD, initial version creation, and version advancement."""
    headers = make_auth_headers(test_user)

    # 1. Create Workflow
    create_payload = {
        "name": "Orange County Comprehensive Search",
        "description": "Searches Assessor & Tax records with partial fault tolerance.",
    }
    resp = await async_client.post("/api/v1/workflows", json=create_payload, headers=headers)
    assert resp.status_code == 201, resp.text
    wf_data = resp.json()
    wf_id = wf_data["id"]
    assert wf_data["name"] == "Orange County Comprehensive Search"
    assert wf_data["current_version_number"] == 1
    assert len(wf_data["versions"]) == 1
    assert wf_data["versions"][0]["is_current"] is True

    # 2. List Workflows
    list_resp = await async_client.get("/api/v1/workflows", headers=headers)
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] >= 1

    # 3. Get Workflow Details
    get_resp = await async_client.get(f"/api/v1/workflows/{wf_id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == wf_id

    # 4. Create New Version (v2)
    new_version_def = {
        "nodes": [
            {"id": "in", "type": "input", "data": {"required_params": ["parcel_id"]}},
            {"id": "tax", "type": "tax", "data": {"allow_failure": True}},
            {"id": "out", "type": "output", "data": {"output_format": "json"}},
        ],
        "edges": [
            {"source": "in", "target": "tax"},
            {"source": "tax", "target": "out"},
        ],
    }
    v2_resp = await async_client.post(
        f"/api/v1/workflows/{wf_id}/versions",
        json={
            "definition": new_version_def,
            "changelog": "Simplified to Tax only search",
            "is_current": True,
        },
        headers=headers,
    )
    assert v2_resp.status_code == 201, v2_resp.text
    v2_data = v2_resp.json()
    assert v2_data["version_number"] == 2
    assert v2_data["is_current"] is True

    # Check that v1 is no longer current and workflow indicates v2
    get_again = await async_client.get(f"/api/v1/workflows/{wf_id}", headers=headers)
    wf_updated = get_again.json()
    assert wf_updated["current_version_number"] == 2
    assert len(wf_updated["versions"]) == 2

    # 5. Set Version 1 as current again
    v1_id = [v["id"] for v in wf_updated["versions"] if v["version_number"] == 1][0]
    set_curr_resp = await async_client.post(
        f"/api/v1/workflows/{wf_id}/versions/{v1_id}/set-current",
        headers=headers,
    )
    assert set_curr_resp.status_code == 200
    assert set_curr_resp.json()["version_number"] == 1
    assert set_curr_resp.json()["is_current"] is True


@pytest.mark.asyncio
async def test_workflow_validation_endpoint(async_client: AsyncClient, test_user: User):
    """Test the /api/v1/workflows/validate endpoint."""
    headers = make_auth_headers(test_user)

    # Valid graph
    valid_payload = {
        "definition": {
            "nodes": [
                {"id": "in", "type": "input"},
                {"id": "assessor", "type": "assessor"},
                {"id": "out", "type": "output"},
            ],
            "edges": [
                {"source": "in", "target": "assessor"},
                {"source": "assessor", "target": "out"},
            ],
        }
    }
    resp = await async_client.post("/api/v1/workflows/validate", json=valid_payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is True
    assert data["supports_assessor"] is True
    assert len(data["execution_order"]) == 3

    # Invalid graph (Missing output)
    invalid_payload = {
        "definition": {
            "nodes": [
                {"id": "in", "type": "input"},
                {"id": "assessor", "type": "assessor"},
            ],
            "edges": [
                {"source": "in", "target": "assessor"},
            ],
        }
    }
    invalid_resp = await async_client.post("/api/v1/workflows/validate", json=invalid_payload, headers=headers)
    assert invalid_resp.status_code == 200
    inv_data = invalid_resp.json()
    assert inv_data["is_valid"] is False
    assert any("output" in err for err in inv_data["errors"])


@pytest.mark.asyncio
async def test_workflow_authorization(
    async_client: AsyncClient, test_user: User, second_user: User
):
    """Test that users cannot modify or delete workflows owned by other users."""
    user1_headers = make_auth_headers(test_user)
    user2_headers = make_auth_headers(second_user)

    # User 1 creates workflow
    create_resp = await async_client.post(
        "/api/v1/workflows",
        json={"name": "User 1 Private Workflow"},
        headers=user1_headers,
    )
    wf_id = create_resp.json()["id"]

    # User 2 tries to update it -> 403 Forbidden
    update_resp = await async_client.put(
        f"/api/v1/workflows/{wf_id}",
        json={"name": "Hijacked Name"},
        headers=user2_headers,
    )
    assert update_resp.status_code == 403

    # User 2 tries to delete it -> 403 Forbidden
    delete_resp = await async_client.delete(
        f"/api/v1/workflows/{wf_id}",
        headers=user2_headers,
    )
    assert delete_resp.status_code == 403

    # User 1 successfully deletes it -> 204 No Content
    delete_ok = await async_client.delete(
        f"/api/v1/workflows/{wf_id}",
        headers=user1_headers,
    )
    assert delete_ok.status_code == 204


@pytest.mark.asyncio
async def test_delete_workflow_with_existing_runs(
    async_client: AsyncClient, test_user: User
):
    """Verify that deleting a workflow with existing WorkflowRun records cascades cleanly without foreign key errors."""
    user_headers = make_auth_headers(test_user)

    # 1. Create a workflow
    create_resp = await async_client.post(
        "/api/v1/workflows",
        json={"name": "Workflow With Runs To Delete"},
        headers=user_headers,
    )
    assert create_resp.status_code == 201
    wf_data = create_resp.json()
    wf_id = wf_data["id"]
    ver_id = wf_data["current_version_id"]

    # 2. Insert a WorkflowRun directly linked to this workflow and version
    async with TestSessionLocal() as session:
        run = WorkflowRun(
            id=uuid.uuid4(),
            workflow_id=uuid.UUID(wf_id),
            workflow_version_id=uuid.UUID(ver_id),
            triggered_by=test_user.id,
            status="COMPLETED",
            input_data={"parcel_id": "TEST-123"},
        )
        session.add(run)
        await session.commit()

    # 3. Delete the workflow via API
    delete_resp = await async_client.delete(
        f"/api/v1/workflows/{wf_id}",
        headers=user_headers,
    )
    assert delete_resp.status_code == 204

    # 4. Confirm workflow and run are gone
    get_resp = await async_client.get(
        f"/api/v1/workflows/{wf_id}",
        headers=user_headers,
    )
    assert get_resp.status_code == 404

    async with TestSessionLocal() as session:
        from sqlalchemy import select
        res = await session.execute(
            select(WorkflowRun).where(WorkflowRun.workflow_id == uuid.UUID(wf_id))
        )
        assert res.scalar_one_or_none() is None
