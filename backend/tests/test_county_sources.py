"""Unit and integration tests for Phase 2B County Source Management."""

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
    RefreshToken,
    SourceConfiguration,
    TemplateVersion,
    UITemplate,
    User,
)

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

COUNTY_TABLES = [
    User.__table__,
    RefreshToken.__table__,
    CountySource.__table__,
    UITemplate.__table__,
    TemplateVersion.__table__,
    SourceConfiguration.__table__,
]


@pytest_asyncio.fixture(scope="function", autouse=True)
async def setup_county_test_db() -> AsyncGenerator[None, None]:
    """Create all county management tables before each test and drop them after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=COUNTY_TABLES))
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=COUNTY_TABLES))


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_users() -> dict[str, dict]:
    """Create two test users in the database and return their tokens."""
    user1_id = uuid.uuid4()
    user2_id = uuid.uuid4()

    async with TestSessionLocal() as session:
        u1 = User(
            id=user1_id,
            email="owner@example.com",
            hashed_password="hashed_pw_dummy",
            full_name="Owner User",
            is_active=True,
            is_superuser=False,
        )
        u2 = User(
            id=user2_id,
            email="other@example.com",
            hashed_password="hashed_pw_dummy",
            full_name="Other User",
            is_active=True,
            is_superuser=False,
        )
        session.add_all([u1, u2])
        await session.commit()

    token1 = create_access_token(subject=str(user1_id))
    token2 = create_access_token(subject=str(user2_id))

    return {
        "user1": {"id": user1_id, "token": token1, "headers": {"Authorization": f"Bearer {token1}"}},
        "user2": {"id": user2_id, "token": token2, "headers": {"Authorization": f"Bearer {token2}"}},
    }


# ---------------------------------------------------------------------------
# County Sources Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_county_source_success(client: AsyncClient, test_users: dict) -> None:
    headers = test_users["user1"]["headers"]
    payload = {
        "name": "Orange County Property Portal",
        "state": "fl",  # Should be normalized to uppercase 'FL'
        "county": "Orange",
        "country": "US",
        "description": "Florida Orange County records",
        "assessor_url": "https://www.ocpafl.org",
        "tax_url": "https://www.octaxcol.com",
        "supported_search_types": ["parcel_id", "address", "owner_name"],
    }
    res = await client.post("/api/v1/county-sources", json=payload, headers=headers)
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "Orange County Property Portal"
    assert data["state"] == "FL"
    assert data["county"] == "Orange"
    assert data["config_status"] == "unconfigured"
    assert data["created_by"] == str(test_users["user1"]["id"])


@pytest.mark.asyncio
async def test_create_county_source_duplicate_rejected(client: AsyncClient, test_users: dict) -> None:
    headers = test_users["user1"]["headers"]
    payload = {
        "name": "Miami-Dade Appraiser",
        "state": "FL",
        "county": "Miami-Dade",
    }
    res1 = await client.post("/api/v1/county-sources", json=payload, headers=headers)
    assert res1.status_code == 201

    # Second attempt with same county + state
    res2 = await client.post("/api/v1/county-sources", json=payload, headers=headers)
    assert res2.status_code == 409
    assert res2.json()["code"] == "DUPLICATE_COUNTY_SOURCE"


@pytest.mark.asyncio
async def test_list_county_sources_pagination_and_filtering(
    client: AsyncClient, test_users: dict
) -> None:
    headers = test_users["user1"]["headers"]

    # Seed 3 sources across states
    sources = [
        {"name": "Brevard Portal", "state": "FL", "county": "Brevard"},
        {"name": "Broward Portal", "state": "FL", "county": "Broward"},
        {"name": "Travis Portal", "state": "TX", "county": "Travis"},
    ]
    for s in sources:
        await client.post("/api/v1/county-sources", json=s, headers=headers)

    # 1. Test state filter
    res_fl = await client.get("/api/v1/county-sources?state=FL", headers=headers)
    assert res_fl.status_code == 200
    fl_data = res_fl.json()
    assert fl_data["total"] == 2
    assert len(fl_data["items"]) == 2

    # 2. Test search filter
    res_search = await client.get("/api/v1/county-sources?search=Travis", headers=headers)
    assert res_search.status_code == 200
    search_data = res_search.json()
    assert search_data["total"] == 1
    assert search_data["items"][0]["county"] == "Travis"

    # 3. Test pagination size
    res_page = await client.get("/api/v1/county-sources?page=1&size=2", headers=headers)
    assert res_page.status_code == 200
    page_data = res_page.json()
    assert len(page_data["items"]) == 2
    assert page_data["total"] == 3
    assert page_data["pages"] == 2


@pytest.mark.asyncio
async def test_get_county_source_by_id(client: AsyncClient, test_users: dict) -> None:
    headers = test_users["user1"]["headers"]
    create_res = await client.post(
        "/api/v1/county-sources",
        json={"name": "Hillsborough Records", "state": "FL", "county": "Hillsborough"},
        headers=headers,
    )
    source_id = create_res.json()["id"]

    res = await client.get(f"/api/v1/county-sources/{source_id}", headers=headers)
    assert res.status_code == 200
    assert res.json()["id"] == source_id
    assert res.json()["county"] == "Hillsborough"


@pytest.mark.asyncio
async def test_update_county_source(client: AsyncClient, test_users: dict) -> None:
    headers = test_users["user1"]["headers"]
    create_res = await client.post(
        "/api/v1/county-sources",
        json={"name": "Initial Name", "state": "CA", "county": "Alameda"},
        headers=headers,
    )
    source_id = create_res.json()["id"]

    update_payload = {
        "name": "Updated Alameda Portal",
        "description": "Updated description",
        "assessor_url": "https://www.acgov.org/assessor",
    }
    res = await client.put(f"/api/v1/county-sources/{source_id}", json=update_payload, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "Updated Alameda Portal"
    assert data["assessor_url"] == "https://www.acgov.org/assessor"


@pytest.mark.asyncio
async def test_authorization_user_cannot_update_or_delete_other_user_source(
    client: AsyncClient, test_users: dict
) -> None:
    headers_user1 = test_users["user1"]["headers"]
    headers_user2 = test_users["user2"]["headers"]

    # User 1 creates source
    create_res = await client.post(
        "/api/v1/county-sources",
        json={"name": "Private Source", "state": "NY", "county": "Kings"},
        headers=headers_user1,
    )
    source_id = create_res.json()["id"]

    # User 2 tries to update -> 403 Forbidden
    res_update = await client.put(
        f"/api/v1/county-sources/{source_id}",
        json={"name": "Malicious Update Attempt"},
        headers=headers_user2,
    )
    assert res_update.status_code == 403
    assert res_update.json()["code"] == "FORBIDDEN_COUNTY_SOURCE"

    # User 2 tries to delete -> 403 Forbidden
    res_delete = await client.delete(f"/api/v1/county-sources/{source_id}", headers=headers_user2)
    assert res_delete.status_code == 403
    assert res_delete.json()["code"] == "FORBIDDEN_COUNTY_SOURCE"

    # User 1 can delete their own source -> 204 No Content
    res_owner_delete = await client.delete(f"/api/v1/county-sources/{source_id}", headers=headers_user1)
    assert res_owner_delete.status_code == 204


@pytest.mark.asyncio
async def test_unauthenticated_request_rejected(client: AsyncClient) -> None:
    # No auth header provided
    res = await client.get("/api/v1/county-sources")
    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHENTICATED"


# ---------------------------------------------------------------------------
# Source Configurations Tests (Assessor & Tax)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_list_source_configurations(client: AsyncClient, test_users: dict) -> None:
    headers = test_users["user1"]["headers"]

    # 1. Create County Source
    create_source = await client.post(
        "/api/v1/county-sources",
        json={"name": "Duval County Portal", "state": "FL", "county": "Duval"},
        headers=headers,
    )
    source_id = create_source.json()["id"]

    # 2. Add Assessor Configuration
    assessor_payload = {
        "source_type": "assessor",
        "website_url": "https://paopropertysearch.coj.net",
        "search_page_url": "https://paopropertysearch.coj.net/Basic/Search.aspx",
        "search_method": "FORM_SUBMIT",
        "search_fields": {
            "parcel_id": {"selector": "#txtParcel", "type": "input"},
            "owner": {"selector": "#txtOwner", "type": "input"},
        },
        "search_button": {"selector": "#btnSearch", "type": "click"},
        "result_page": {"result_container": ".result-table"},
        "field_mappings": {"assessed_value": ".val-assessed", "legal_desc": ".val-legal"},
        "status": "draft",
    }
    res_assessor = await client.post(
        f"/api/v1/county-sources/{source_id}/configurations",
        json=assessor_payload,
        headers=headers,
    )
    assert res_assessor.status_code == 201
    assessor_data = res_assessor.json()
    assert assessor_data["source_type"] == "assessor"
    assert assessor_data["county_source_id"] == source_id

    # Check parent source status updated to 'partial'
    source_check = await client.get(f"/api/v1/county-sources/{source_id}", headers=headers)
    assert source_check.json()["config_status"] == "partial"

    # 3. Add Tax Configuration
    tax_payload = {
        "source_type": "tax",
        "website_url": "https://duval.county-taxes.com",
        "search_page_url": "https://duval.county-taxes.com/public",
        "search_method": "FORM_SUBMIT",
        "search_fields": {"parcel_id": {"selector": "input[name='query']"}},
        "status": "draft",
    }
    res_tax = await client.post(
        f"/api/v1/county-sources/{source_id}/configurations",
        json=tax_payload,
        headers=headers,
    )
    assert res_tax.status_code == 201
    assert res_tax.json()["source_type"] == "tax"

    # Check parent source status updated to 'configured' (both present)
    source_check2 = await client.get(f"/api/v1/county-sources/{source_id}", headers=headers)
    assert source_check2.json()["config_status"] == "configured"
    assert len(source_check2.json()["configurations"]) == 2

    # 4. List configurations endpoint
    list_configs = await client.get(
        f"/api/v1/county-sources/{source_id}/configurations", headers=headers
    )
    assert list_configs.status_code == 200
    assert len(list_configs.json()) == 2


@pytest.mark.asyncio
async def test_update_and_delete_configuration(client: AsyncClient, test_users: dict) -> None:
    headers = test_users["user1"]["headers"]

    # Create source
    src = await client.post(
        "/api/v1/county-sources",
        json={"name": "Pinellas Records", "state": "FL", "county": "Pinellas"},
        headers=headers,
    )
    source_id = src.json()["id"]

    # Add config
    cfg = await client.post(
        f"/api/v1/county-sources/{source_id}/configurations",
        json={"source_type": "assessor", "website_url": "https://www.pcpao.org"},
        headers=headers,
    )
    config_id = cfg.json()["id"]

    # Update config
    update_res = await client.put(
        f"/api/v1/county-sources/{source_id}/configurations/{config_id}",
        json={"search_method": "SPA_INTERACTION", "status": "active"},
        headers=headers,
    )
    assert update_res.status_code == 200
    assert update_res.json()["search_method"] == "SPA_INTERACTION"
    assert update_res.json()["status"] == "active"

    # Delete config
    del_res = await client.delete(
        f"/api/v1/county-sources/{source_id}/configurations/{config_id}",
        headers=headers,
    )
    assert del_res.status_code == 204

    # Verify source returns to unconfigured
    source_check = await client.get(f"/api/v1/county-sources/{source_id}", headers=headers)
    assert source_check.json()["config_status"] == "unconfigured"

