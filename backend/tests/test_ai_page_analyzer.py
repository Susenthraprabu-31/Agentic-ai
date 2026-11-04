"""Tests for AI Page Analyzer and multi-tab input-aware recipe automation."""

import uuid
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
from app.services.ai_page_analyzer import AIPageAnalyzerService

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
]


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=TABLES))
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=TABLES))


async def override_get_db():
    async with TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def async_client():
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
            email="ai_tester@example.com",
            hashed_password="hashed_pw_dummy",
            full_name="AI Tester",
            is_active=True,
            is_superuser=False,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.mark.asyncio
async def test_ai_page_analyzer_synthesis(test_user: User):
    """Verify that AIPageAnalyzer synthesizes multi-tab conditional steps from DOM structures."""
    async with TestSessionLocal() as session:
        analyzer = AIPageAnalyzerService(session)

        mock_dom = {
            "has_disclaimer": True,
            "tabs": [
                {"text": "Owner", "selector": "a[href='#TabOwner']", "href": "#TabOwner", "id": "ui-id-9"},
                {"text": "Site Address", "selector": "a[href='#TabAddress']", "href": "#TabAddress", "id": "ui-id-10"},
                {"text": "Parcel ID", "selector": "a[href='#TabParcel']", "href": "#TabParcel", "id": "ui-id-11"},
            ],
            "inputs": [
                {"id": "OwnerLine1", "name": "OwnerLine1", "type": "text", "selector": "#OwnerLine1"},
                {"id": "SiteStreetAddress", "name": "SiteStreetAddress", "type": "text", "selector": "#SiteStreetAddress"},
                {"id": "ParcelID", "name": "ParcelID", "type": "text", "selector": "#ParcelID"},
            ],
            "buttons": [
                {"id": "SearchName1", "name": "SearchName1", "text": "Search", "selector": "#SearchName1"},
                {"id": "SearchFullAddress", "name": "SearchFullAddress", "text": "Search", "selector": "#SearchFullAddress"},
                {"id": "SearchParcelID", "name": "SearchParcelID", "text": "Search", "selector": "#SearchParcelID"},
            ],
        }

        recipe_def = analyzer._synthesize_recipe_definition(
            dom=mock_dom,
            portal_url="https://example.com/search",
            county_name="Collier",
            record_type="assessor",
        )

        steps = recipe_def["steps"]
        assert len(steps) >= 10

        # Check Parcel ID branch
        parcel_steps = [s for s in steps if s.get("condition") == "input.parcel_id"]
        assert len(parcel_steps) == 4
        assert parcel_steps[0]["action"] == "click"
        assert parcel_steps[0]["selector"] == "a[href='#TabParcel']"
        assert parcel_steps[2]["action"] == "fill"
        assert parcel_steps[2]["selector"] == "#ParcelID"
        assert parcel_steps[3]["action"] == "click"
        assert parcel_steps[3]["selector"] == "#SearchParcelID"

        # Check Address branch
        address_steps = [s for s in steps if s.get("condition") == "input.address"]
        assert len(address_steps) == 4
        assert address_steps[0]["action"] == "click"
        assert address_steps[0]["selector"] == "a[href='#TabAddress']"

        # Check Owner branch
        owner_steps = [s for s in steps if s.get("condition") == "input.owner_name"]
        assert len(owner_steps) == 4
        assert owner_steps[0]["action"] == "click"
        assert owner_steps[0]["selector"] == "a[href='#TabOwner']"


@pytest.mark.asyncio
async def test_step_runner_condition_evaluation():
    """Verify that StepRunner correctly evaluates conditions for parcel_id, address, and owner_name."""
    from app.browser.step_runner import PlaywrightStepRunner

    class MockPage:
        pass

    runner = PlaywrightStepRunner(MockPage(), "dummy_run", "dummy_node")

    context_parcel = {"input": {"parcel_id": "37540640003", "address": "", "owner_name": ""}}
    assert runner._evaluate_condition("input.parcel_id", context_parcel) is True
    assert runner._evaluate_condition("input.address", context_parcel) is False
    assert runner._evaluate_condition("input.owner_name", context_parcel) is False

    context_address = {"input": {"parcel_id": "", "address": "780 14TH AVE NW", "owner_name": ""}}
    assert runner._evaluate_condition("input.parcel_id", context_address) is False
    assert runner._evaluate_condition("input.address", context_address) is True
    assert runner._evaluate_condition("input.owner_name", context_address) is False

    context_owner = {"input": {"parcel_id": "", "address": "", "owner_name": "SMITH JOHN"}}
    assert runner._evaluate_condition("input.parcel_id", context_owner) is False
    assert runner._evaluate_condition("input.address", context_owner) is False
    assert runner._evaluate_condition("input.owner_name", context_owner) is True

    # Test compound conditions (&& and !)
    assert runner._evaluate_condition("input.address && !input.parcel_id", context_address) is True
    assert runner._evaluate_condition("input.address && !input.parcel_id", context_parcel) is False
    assert runner._evaluate_condition("input.owner_name && !input.parcel_id && !input.address", context_owner) is True
    assert runner._evaluate_condition("input.owner_name && !input.parcel_id && !input.address", context_address) is False


@pytest.mark.asyncio
async def test_dynamic_recipe_synthesis_with_executed_plan():
    """Verify that AIPageAnalyzerService compiles executed AI plan, radio selectors, and autocomplete."""
    from app.services.ai_page_analyzer import AIPageAnalyzerService

    analyzer = AIPageAnalyzerService(db=None)

    mock_dom = {
        "elements": [
            {
                "kind": "radio",
                "selector": 'input[type="radio"][name="basicPinGroup"][value="folio"]',
                "label": "Folio",
                "value": "folio"
            },
            {
                "kind": "input",
                "selector": 'input[placeholder="EX:121415-1214"]',
                "placeholder": "EX:121415-1214"
            },
            {
                "kind": "search_button",
                "selector": 'button:has-text("Search")',
                "text": "Search"
            }
        ]
    }

    executed_plan = {
        "query_type": "parcel",
        "tab_selector": None,
        "radio_selector": 'input[type="radio"][name="basicPinGroup"][value="folio"]',
        "input_selector": 'input[placeholder="EX:121415-1214"]',
        "submit_selector": 'button:has-text("Search")',
    }

    autocomplete_act = {
        "detected": True,
        "selector": ".ui-autocomplete li:visible",
        "selected_text": "161425-0000"
    }

    result_click_act = {
        "is_results_page": True,
        "clicked": True,
        "selector": 'td[data-bind*="goTo"]:has-text("161425-0000")',
    }

    recipe_def = analyzer._synthesize_recipe_definition(
        dom=mock_dom,
        portal_url="https://gis.hcpafl.org/PropertySearch/#/nav/Basic%20Search",
        county_name="Hillsborough",
        record_type="assessor",
        executed_plan=executed_plan,
        autocomplete_action=autocomplete_act,
        result_click_action=result_click_act,
    )

    steps = recipe_def["steps"]
    parcel_steps = [s for s in steps if s.get("condition") == "input.parcel_id"]

    # Verify radio selector step
    radio_step = next((s for s in parcel_steps if s.get("selector") == executed_plan["radio_selector"]), None)
    assert radio_step is not None
    assert radio_step["action"] == "click_if_visible"

    # Verify input fill step uses exact format placeholder selector
    fill_step = next((s for s in parcel_steps if s.get("action") == "fill"), None)
    assert fill_step is not None
    assert fill_step["selector"] == 'input[placeholder="EX:121415-1214"]'

    # Verify autocomplete step is present
    ac_step = next((s for s in parcel_steps if ".ui-autocomplete" in (s.get("selector") or "")), None)
    assert ac_step is not None
    assert ac_step["action"] == "click_if_visible"

    # Verify submit step
    submit_step = next((s for s in parcel_steps if s.get("action") == "click" and s.get("selector") == 'button:has-text("Search")'), None)
    assert submit_step is not None

    # Verify result click step contains the clicked cell selector
    res_click_step = next((s for s in steps if "data-bind" in (s.get("selector") or "")), None)
    assert res_click_step is not None
    assert 'td[data-bind*="goTo"]:has-text("161425-0000")' in res_click_step["selector"]


@pytest.mark.asyncio
async def test_plan_search_excludes_containers_and_social_links():
    """Verify that PageSearchAI never selects container divs or social links as submit buttons."""
    from app.browser.page_search_ai import PageSearchAI

    ai = PageSearchAI(db=None)
    mock_snapshot = {
        "url": "https://qpublic.schneidercorp.com/Application.aspx?AppID=867",
        "title": "Dixie County Property Search",
        "elements": [
            {
                "kind": "search_button",
                "selector": "#tabRow",
                "tag": "div",
                "text": "Map Search Sales List",
                "id": "tabRow",
            },
            {
                "kind": "button",
                "selector": "#aFacebook",
                "tag": "a",
                "text": "Share on Facebook",
                "id": "aFacebook",
            },
            {
                "kind": "input",
                "selector": 'input[placeholder="enter parcel number..."]',
                "tag": "input",
                "placeholder": "enter parcel number...",
                "id": "ctlBodyPane_ctl02_ctl01_txtParcelID",
            },
            {
                "kind": "search_button",
                "selector": "#ctlBodyPane_ctl02_ctl01_btnSearch",
                "tag": "a",
                "text": "Search",
                "id": "ctlBodyPane_ctl02_ctl01_btnSearch",
            },
        ],
    }

    plan = await ai.plan_search(mock_snapshot, query_type="parcel", query_value="14-12-10-0614-0052-0020")
    assert plan["input_selector"] == 'input[placeholder="enter parcel number..."]'
    assert plan["submit_selector"] != "#tabRow"
    assert "facebook" not in (plan["submit_selector"] or "").lower()
    assert plan["submit_selector"] == "#ctlBodyPane_ctl02_ctl01_btnSearch"



