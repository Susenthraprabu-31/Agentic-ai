"""Unit and integration tests for Phase 2C Recipe Template Management."""

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

RECIPE_TABLES = [
    User.__table__,
    RefreshToken.__table__,
    CountySource.__table__,
    UITemplate.__table__,
    TemplateVersion.__table__,
    SourceConfiguration.__table__,
    Recipe.__table__,
    RecipeVersion.__table__,
    ValidationCase.__table__,
]


@pytest_asyncio.fixture(scope="function", autouse=True)
async def setup_recipe_test_db() -> AsyncGenerator[None, None]:
    """Create all recipe management tables before each test and drop them after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=RECIPE_TABLES))
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=RECIPE_TABLES))


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
async def test_setup() -> dict:
    """Create test users and a county source."""
    user1_id = uuid.uuid4()
    user2_id = uuid.uuid4()
    source_id = uuid.uuid4()

    async with TestSessionLocal() as session:
        u1 = User(id=user1_id, email="chef@example.com", hashed_password="pw", is_active=True, is_superuser=False)
        u2 = User(id=user2_id, email="other@example.com", hashed_password="pw", is_active=True, is_superuser=False)
        src = CountySource(
            id=source_id,
            name="Leon County Portal",
            state="FL",
            county="Leon",
            created_by=user1_id,
        )
        session.add_all([u1, u2, src])
        await session.commit()

    token1 = create_access_token(subject=str(user1_id))
    token2 = create_access_token(subject=str(user2_id))

    return {
        "user1": {"id": user1_id, "headers": {"Authorization": f"Bearer {token1}"}},
        "user2": {"id": user2_id, "headers": {"Authorization": f"Bearer {token2}"}},
        "source_id": str(source_id),
    }


# ---------------------------------------------------------------------------
# UI Templates & Template Versions Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ui_template_crud_and_versioning(client: AsyncClient, test_setup: dict) -> None:
    headers = test_setup["user1"]["headers"]

    # 1. Create Template
    create_payload = {
        "name": "Standard Search Form Template",
        "description": "Standard portal with single parcel input",
        "pattern_type": "STANDARD_FORM",
        "supported_source_types": ["assessor", "tax"],
        "supported_search_methods": ["FORM_SUBMIT"],
        "required_configuration_fields": ["parcel_id"],
        "tags": ["standard", "single-form"],
    }
    res = await client.post("/api/v1/recipe-templates", json=create_payload, headers=headers)
    assert res.status_code == 201
    tpl = res.json()
    tpl_id = tpl["id"]
    assert tpl["name"] == "Standard Search Form Template"
    assert len(tpl["versions"]) == 1
    assert tpl["versions"][0]["version_number"] == 1

    # 2. Add a new template version
    v_res = await client.post(
        f"/api/v1/recipe-templates/{tpl_id}/versions",
        json={"definition": {"interaction": "click-then-type"}, "changelog": "Added wait step"},
        headers=headers,
    )
    assert v_res.status_code == 201
    assert v_res.json()["version_number"] == 2

    # 3. List templates
    list_res = await client.get("/api/v1/recipe-templates", headers=headers)
    assert list_res.status_code == 200
    assert list_res.json()["total"] == 1

    # 4. Update template
    upd_res = await client.put(
        f"/api/v1/recipe-templates/{tpl_id}",
        json={"name": "Renamed Template"},
        headers=headers,
    )
    assert upd_res.status_code == 200
    assert upd_res.json()["name"] == "Renamed Template"


# ---------------------------------------------------------------------------
# Recipe Definition Validation & Unsafe Detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recipe_definition_validation(client: AsyncClient, test_setup: dict) -> None:
    headers = test_setup["user1"]["headers"]
    source_id = test_setup["source_id"]

    # 1. Valid declarative recipe definition
    valid_def = {
        "search": {
            "method": "FORM_SUBMIT",
            "fields": [
                {"name": "parcel_id", "locator": {"strategy": "css", "value": "#parcelNumber"}}
            ],
            "submit": {"strategy": "role", "value": "button"},
        },
        "results": {"type": "table"},
        "field_mappings": {"parcel_number": "Parcel ID"},
    }
    res = await client.post(
        "/api/v1/recipes",
        json={
            "name": "Valid Recipe",
            "county_source_id": source_id,
            "record_type": "assessor",
            "initial_definition": valid_def,
        },
        headers=headers,
    )
    assert res.status_code == 201

    # 2. Invalid definition — unsupported locator strategy
    invalid_def = {
        "search": {
            "fields": [
                {"name": "parcel_id", "locator": {"strategy": "magic_ai_locator", "value": "#val"}}
            ]
        }
    }
    bad_res = await client.post(
        "/api/v1/recipes",
        json={
            "name": "Bad Recipe",
            "county_source_id": source_id,
            "record_type": "assessor",
            "initial_definition": invalid_def,
        },
        headers=headers,
    )
    assert bad_res.status_code == 422
    assert bad_res.json()["code"] == "UNSUPPORTED_LOCATOR_STRATEGY"

    # 3. Unsafe definition — dangerous code pattern injection
    unsafe_def = {
        "search": {
            "fields": [
                {"name": "exec_test", "locator": {"strategy": "css", "value": "exec(evil_code)"}}
            ]
        }
    }
    unsafe_res = await client.post(
        "/api/v1/recipes",
        json={
            "name": "Unsafe Recipe",
            "county_source_id": source_id,
            "record_type": "assessor",
            "initial_definition": unsafe_def,
        },
        headers=headers,
    )
    assert unsafe_res.status_code == 422
    assert unsafe_res.json()["code"] == "UNSAFE_RECIPE_DEFINITION"


# ---------------------------------------------------------------------------
# Template & Source Configuration Compatibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_template_compatibility_enforcement(client: AsyncClient, test_setup: dict) -> None:
    headers = test_setup["user1"]["headers"]
    source_id = test_setup["source_id"]

    # 1. Create a template that requires 'parcel_id' and method 'POST'
    tpl_res = await client.post(
        "/api/v1/recipe-templates",
        json={
            "name": "Strict Post Template",
            "supported_source_types": ["assessor"],
            "supported_search_methods": ["POST"],
            "required_configuration_fields": ["parcel_id"],
        },
        headers=headers,
    )
    tpl_id = tpl_res.json()["id"]

    # 2. Create an incompatible configuration (uses GET and missing parcel_id)
    cfg_res = await client.post(
        f"/api/v1/county-sources/{source_id}/configurations",
        json={
            "source_type": "assessor",
            "website_url": "https://leonpa.org",
            "search_method": "GET",
            "search_fields": {"owner": {"selector": "#owner"}},
        },
        headers=headers,
    )
    cfg_id = cfg_res.json()["id"]

    # 3. Attempt to create recipe binding this incompatible template and config -> 422
    incompat_res = await client.post(
        "/api/v1/recipes",
        json={
            "name": "Incompatible Recipe",
            "county_source_id": source_id,
            "record_type": "assessor",
            "ui_template_id": tpl_id,
            "source_configuration_id": cfg_id,
        },
        headers=headers,
    )
    assert incompat_res.status_code == 422
    assert incompat_res.json()["code"] == "INCOMPATIBLE_TEMPLATE_CONFIGURATION"


# ---------------------------------------------------------------------------
# Recipe Lifecycle & Immutability of Published Versions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recipe_lifecycle_and_immutability(client: AsyncClient, test_setup: dict) -> None:
    headers = test_setup["user1"]["headers"]
    source_id = test_setup["source_id"]

    # 1. Create Recipe
    res = await client.post(
        "/api/v1/recipes",
        json={
            "name": "Lifecycle Test Recipe",
            "county_source_id": source_id,
            "record_type": "assessor",
        },
        headers=headers,
    )
    recipe = res.json()
    recipe_id = recipe["id"]
    version_1_id = recipe["versions"][0]["id"]
    assert recipe["versions"][0]["lifecycle_status"] == "draft"

    # 2. Attempt to publish directly from draft -> Must fail (Must be validated or approved first)
    pub_fail = await client.post(
        f"/api/v1/recipes/{recipe_id}/versions/{version_1_id}/publish", headers=headers
    )
    assert pub_fail.status_code == 422
    assert pub_fail.json()["code"] == "INVALID_LIFECYCLE_TRANSITION"

    # 3. Add and pass a validation case
    val_case_res = await client.post(
        f"/api/v1/recipes/{recipe_id}/validation-cases",
        json={
            "name": "Accuracy Verification",
            "input_data": {"parcel": "001"},
            "expected_output": {"owner": "Test Owner"},
        },
        headers=headers,
    )
    val_case_id = val_case_res.json()["id"]

    # Simulate validation pass
    await client.put(
        f"/api/v1/recipes/{recipe_id}/validation-cases/{val_case_id}",
        json={"last_passed": True},
        headers=headers,
    )

    # 4. Now publish Version 1 -> Success!
    pub_res = await client.post(
        f"/api/v1/recipes/{recipe_id}/versions/{version_1_id}/publish", headers=headers
    )
    assert pub_res.status_code == 200
    pub_version = pub_res.json()
    assert pub_version["is_published"] is True
    assert pub_version["lifecycle_status"] == "published"
    assert pub_version["published_at"] is not None

    # 5. IMMUTABILITY CHECK: Attempt to update published version definition -> Must fail (409)
    mod_attempt = await client.put(
        f"/api/v1/recipes/{recipe_id}/versions/{version_1_id}",
        json={"definition": {"results": {"type": "table"}}},
        headers=headers,
    )
    assert mod_attempt.status_code == 409
    assert mod_attempt.json()["code"] == "IMMUTABLE_PUBLISHED_VERSION"

    # 6. User creates Version 2 to make further changes
    v2_res = await client.post(
        f"/api/v1/recipes/{recipe_id}/versions",
        json={"definition": {"results": {"type": "cards"}}, "notes": "Upgraded to cards format"},
        headers=headers,
    )
    assert v2_res.status_code == 201
    v2 = v2_res.json()
    assert v2["version_number"] == 2
    assert v2["lifecycle_status"] == "draft"
    assert v2["is_published"] is False

    # 7. Deprecate Version 1 without deleting it
    dep_res = await client.post(
        f"/api/v1/recipes/{recipe_id}/versions/{version_1_id}/deprecate", headers=headers
    )
    assert dep_res.status_code == 200
    assert dep_res.json()["lifecycle_status"] == "deprecated"

    # Version history remains intact
    hist_res = await client.get(f"/api/v1/recipes/{recipe_id}/versions", headers=headers)
    assert len(hist_res.json()) == 2

