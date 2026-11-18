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


@pytest.mark.asyncio
async def test_address_search_prefers_mailing_address_over_altkey():
    """Multi-field portals (e.g. CommonSearch) must target Mailing Address, not Altkey/parcel."""
    from app.browser.page_search_ai import PageSearchAI, build_search_fills

    citrus_like = {
        "url": "https://example.gov/commonsearch.aspx?mode=realprop",
        "title": "Real Property Search",
        "elements": [
            {"kind": "input", "selector": "#inpAltkey", "label": "Altkey", "id": "inpAltkey"},
            {"kind": "input", "selector": "#inpName", "label": "Name", "id": "inpName"},
            {
                "kind": "input",
                "selector": "#inpMailAddr",
                "label": "Mailing Address",
                "id": "inpMailAddr",
            },
            {"kind": "input", "selector": "#inpStrNum", "label": "Street #", "id": "inpStrNum"},
            {"kind": "input", "selector": "#inpStrName", "label": "Street Name", "id": "inpStrName"},
            {
                "kind": "search_button",
                "selector": 'input[type="submit"][value="Search"]',
                "text": "Search",
                "type": "submit",
                "tag": "input",
            },
        ],
    }

    fills = build_search_fills(citrus_like, "address", "625 N HEATHROW DR")
    assert fills
    assert any(f["selector"] == "#inpStrNum" and f["value"] == "625" for f in fills)
    assert any(f["selector"] == "#inpStrName" and "HEATHROW" in f["value"] for f in fills)

    ai = PageSearchAI(db=None)
    plan = await ai.plan_search(citrus_like, query_type="address", query_value="625 N HEATHROW DR")
    assert plan["input_selector"] == "#inpStrNum"
    submit = (plan.get("submit_selector") or "").lower()
    assert "search" in submit
    assert "parcel search" not in submit
    assert plan.get("alternate_fills")
    mailing = next(f for f in plan["alternate_fills"] if f.get("field_role") == "mailing_line")
    assert mailing["selector"] == "#inpMailAddr"
    assert "625" in mailing["value"] and "HEATHROW" in mailing["value"]
    assert plan.get("address_search_mode") == "try_structured_then_mailing"


def test_parcel_search_blocks_alt_key_field():
    from app.browser.page_search_ai import build_search_fills, _forbidden_input_for_query

    desoto_like = {
        "url": "https://www.desotopa.com/gis/",
        "elements": [
            {"kind": "input", "selector": "#inpAltKey", "label": "Alt Key", "id": "inpAltKey"},
            {
                "kind": "input",
                "selector": "#inpParcel",
                "label": "Parcel Number",
                "placeholder": "##-##-##-####-####-#### (3 min)",
                "id": "inpParcel",
            },
            {"kind": "input", "selector": "#inpOwnerLast", "label": "LastName", "id": "inpOwnerLast"},
        ],
    }
    assert _forbidden_input_for_query(desoto_like["elements"][0], "parcel") is True
    fills = build_search_fills(desoto_like, "parcel", "21-37-34-0788-0000-4590")
    assert fills
    assert fills[0]["selector"] == "#inpParcel"
    assert "alt" not in fills[0]["selector"].lower()


def test_qpublic_address_uses_single_location_field():
    from app.browser.page_search_ai import build_search_fills

    clay = {
        "url": "https://qpublic.schneidercorp.com/Application.aspx?AppID=830",
        "elements": [
            {
                "kind": "input",
                "selector": "#ctlBodyPane_ctl04_ctl01_txtAddress",
                "id": "ctlBodyPane_ctl04_ctl01_txtAddress",
                "placeholder": "Enter address or range of address",
                "layout_top": 360,
                "section_label": "Search by Location Address",
            },
            {"kind": "input", "selector": "#ctlBodyPane_ctl02_ctl01_txtOwner", "placeholder": "enter name..."},
        ],
    }
    fills = build_search_fills(clay, "address", "4574 ALAN LAKE Rd")
    assert len(fills) == 1
    assert fills[0]["selector"] == "#ctlBodyPane_ctl04_ctl01_txtAddress"
    assert "4574" in fills[0]["value"]


def test_beacon_address_rejects_owner_panel_toggle():
    from app.browser.page_search_ai import refine_plan_from_snapshot

    beacon = {
        "url": "https://beacon.schneidercorp.com/Application.aspx?AppID=1105&PageTypeID=2",
        "elements": [
            {
                "kind": "input",
                "id": "ctlBodyPane_ctl00_ctl01_txtOwner",
                "selector": "#ctlBodyPane_ctl00_ctl01_txtOwner",
                "placeholder": "enter name...",
                "section_label": "Search by Owner Name",
            },
            {
                "kind": "tab",
                "id": "ctlBodyPane_ctl00_btnHeaderToggle",
                "selector": "#ctlBodyPane_ctl00_btnHeaderToggle",
            },
            {
                "kind": "input",
                "id": "ctlBodyPane_ctl02_ctl01_txtAddress",
                "selector": "#ctlBodyPane_ctl02_ctl01_txtAddress",
                "placeholder": "enter address...",
                "aria-label": "Search by Location Address",
                "section_label": "Search by Location Address",
            },
            {
                "kind": "search_button",
                "id": "ctlBodyPane_ctl02_ctl01_btnSearch",
                "selector": "#ctlBodyPane_ctl02_ctl01_btnSearch",
            },
            {
                "kind": "tab",
                "text": "Results",
                "selector": "a:has-text(\"Results\")",
                "href": "/Application.aspx?PageTypeID=3",
            },
        ],
    }
    plan = refine_plan_from_snapshot(
        {"tab_selector": "a:has-text(\"Results\")", "input_selector": "#ctlBodyPane_ctl00_ctl01_txtOwner"},
        beacon,
        "address",
        "5154 WATERFRONT WAY",
    )
    assert plan["tab_selector"] == "#ctlBodyPane_ctl02_btnHeaderToggle"
    assert plan["input_selector"] == "#ctlBodyPane_ctl02_ctl01_txtAddress"
    assert plan["submit_selector"] == "#ctlBodyPane_ctl02_ctl01_btnSearch"


def test_section_toggle_matches_location_address_panel():
    from app.browser.page_search_ai import refine_plan_from_snapshot

    gadsden_like = {
        "url": "https://qpublic.schneidercorp.com/Application.aspx?App=GadsdenCountyFL&PageType=Search",
        "elements": [
            {
                "kind": "input",
                "selector": "#ctlBodyPane_ctl00_ctl01_txtOwner",
                "id": "ctlBodyPane_ctl00_ctl01_txtOwner",
                "placeholder": "enter name...",
                "section_label": "Search by Owner Name",
                "layout_top": 120,
            },
            {
                "kind": "input",
                "selector": "#ctlBodyPane_ctl02_ctl01_txtAddress",
                "id": "ctlBodyPane_ctl02_ctl01_txtAddress",
                "placeholder": "enter address...",
                "aria-label": "Search by Location Address",
                "section_label": "Search by Location Address",
                "layout_top": 280,
            },
            {
                "kind": "search_button",
                "selector": "#ctlBodyPane_ctl02_ctl01_btnSearch",
                "id": "ctlBodyPane_ctl02_ctl01_btnSearch",
                "section_label": "Search by Location Address",
            },
            {
                "kind": "tab",
                "selector": "#ctlBodyPane_ctl02_btnHeaderToggle",
                "id": "ctlBodyPane_ctl02_btnHeaderToggle",
                "text": "Search by Location Address",
            },
        ],
    }
    plan = refine_plan_from_snapshot({}, gadsden_like, "address", "201 1ST ST NW")
    assert plan["input_selector"] == "#ctlBodyPane_ctl02_ctl01_txtAddress"
    assert plan["tab_selector"] == "#ctlBodyPane_ctl02_btnHeaderToggle"
    assert plan["submit_selector"] == "#ctlBodyPane_ctl02_ctl01_btnSearch"


def test_submit_pairs_with_location_address_panel():
    """Stacked qPublic panels must click the Search beside the filled address field."""
    from app.browser.page_search_ai import refine_plan_from_snapshot

    snapshot = {
        "elements": [
            {
                "kind": "input",
                "selector": "#ctlBodyPane_ctl02_ctl01_txtOwner",
                "id": "ctlBodyPane_ctl02_ctl01_txtOwner",
                "placeholder": "enter name...",
                "layout_top": 180,
                "panel_id": "ctlBodyPane_ctl02",
                "section_label": "Search by Owner Name",
            },
            {
                "kind": "search_button",
                "selector": "#ctlBodyPane_ctl02_ctl01_btnSearch",
                "id": "ctlBodyPane_ctl02_ctl01_btnSearch",
                "text": "Search",
                "layout_top": 185,
                "panel_id": "ctlBodyPane_ctl02",
                "section_label": "Search by Owner Name",
            },
            {
                "kind": "input",
                "selector": "#ctlBodyPane_ctl04_ctl01_txtAddress",
                "id": "ctlBodyPane_ctl04_ctl01_txtAddress",
                "placeholder": "Enter address or range of address",
                "layout_top": 360,
                "panel_id": "ctlBodyPane_ctl04",
                "section_label": "Search by Location Address",
            },
            {
                "kind": "search_button",
                "selector": "#ctlBodyPane_ctl04_ctl01_btnSearch",
                "id": "ctlBodyPane_ctl04_ctl01_btnSearch",
                "text": "Search",
                "layout_top": 365,
                "panel_id": "ctlBodyPane_ctl04",
                "section_label": "Search by Location Address",
            },
        ]
    }
    plan = refine_plan_from_snapshot(
        {"submit_selector": 'button:has-text("Search")'},
        snapshot,
        "address",
        "4574 ALAN LAKE Rd",
    )
    assert plan["input_selector"] == "#ctlBodyPane_ctl04_ctl01_txtAddress"
    assert plan["submit_selector"] == "#ctlBodyPane_ctl04_ctl01_btnSearch"


def test_build_mailing_line_fills_full_address():
    from app.browser.page_search_ai import build_mailing_line_fills

    portal = {
        "elements": [
            {"kind": "input", "selector": "#mail", "label": "Mailing Address"},
            {"kind": "select", "selector": "#tax", "label": "Tax Year"},
        ]
    }
    fills = build_mailing_line_fills(portal, "825 N HEATHROW DR")
    assert fills[0]["value"] == "825 N HEATHROW DR"
    assert fills[0]["field_role"] == "mailing_line"


def test_parse_street_address_includes_direction():
    from app.browser.page_search_ai import _parse_street_address

    p = _parse_street_address("825 N HEATHROW DR")
    assert p["street_number"] == "825"
    assert p["direction"].upper() == "N"
    assert p["street_name"] == "HEATHROW"
    assert p.get("suffix", "").upper() == "DR"


def test_result_row_token_match_for_address():
    from app.browser.result_selector_ai import _query_match_tokens, _score_row_against_query

    tokens = _query_match_tokens("825 N HEATHROW DR", "address")
    row = "1627753 18E18S310020 003E0 0230 DODGE JOHN A 825 N HEATHROW DR LECANTO"
    score = _score_row_against_query(row, tokens)
    assert "825" in tokens
    assert "heathrow" in tokens
    assert score >= 0.5


def test_address_search_blocks_altkey_field():
    from app.browser.page_search_ai import build_search_fills, _forbidden_input_for_query

    portal = {
        "elements": [
            {"kind": "input", "selector": "#alt", "label": "Altkey", "layout_top": 200},
            {"kind": "input", "selector": "#mail", "label": "Mailing Address", "layout_top": 350},
            {"kind": "input", "selector": "#num", "label": "Street #", "layout_top": 210},
            {"kind": "input", "selector": "#name", "label": "Street Name", "layout_top": 210},
        ]
    }
    assert _forbidden_input_for_query(portal["elements"][0], "address") is True
    fills = build_search_fills(portal, "address", "625 N HEATHROW DR")
    assert fills
    assert all("alt" not in f["selector"].lower() for f in fills)


def test_build_runtime_steps_from_address_plan():
    from app.browser.page_search_ai import build_runtime_steps_from_plan

    plan = {
        "query_type": "address",
        "tab_selector": "#TabAddress",
        "fills": [
            {"selector": "#strNum", "value": "625"},
            {"selector": "#strName", "value": "HEATHROW"},
        ],
        "submit_selector": 'input[type="submit"][value="Search"]',
    }
    steps = build_runtime_steps_from_plan(plan)
    actions = [s["action"] for s in steps]
    assert "click_if_visible" in actions
    assert actions.count("fill") == 2
    assert any(s.get("selector") == 'input[type="submit"][value="Search"]' for s in steps if s["action"] == "click")


@pytest.mark.asyncio
async def test_submit_scoring_rejects_parcel_search_nav_link():
    from app.browser.page_search_ai import _score_submit_button

    nav = {
        "kind": "search_button",
        "tag": "a",
        "text": "Parcel Search",
        "href": "/_Web/search/commonsearch.aspx",
    }
    submit = {
        "kind": "search_button",
        "tag": "input",
        "text": "Search",
        "type": "submit",
    }
    assert _score_submit_button(nav) < 0
    assert _score_submit_button(submit) > _score_submit_button(nav)


def test_match_snapshot_input_selector_placeholder_spacing():
    from app.browser.page_search_ai import match_snapshot_input_selector

    elements = [
        {
            "kind": "input",
            "id": "AddrLookup",
            "placeholder": "Ex: 33617",
            "label": "Address:",
            "selector": 'input[placeholder="Ex: 33617"]',
        }
    ]
    resolved = match_snapshot_input_selector('input[placeholder="EX: 33617"]', elements)
    assert resolved == "#AddrLookup"


def test_hillsborough_address_zip_placeholder_not_forbidden():
    from app.browser.page_search_ai import _forbidden_input_for_query, build_search_fills

    addr = {
        "kind": "input",
        "id": "AddrLookup",
        "placeholder": "Ex: 33617",
        "label": "Address:",
        "selector": 'input[placeholder="Ex: 33617"]',
    }
    assert not _forbidden_input_for_query(addr, "address")
    snap = {
        "elements": [
            addr,
            {
                "kind": "input",
                "selector": 'input[placeholder="EX:121415-1214"]',
                "placeholder": "EX:121415-1214",
                "label": "Folio",
            },
            {"kind": "search_button", "text": "Search", "selector": 'button:has-text("Search")'},
        ]
    }
    fills = build_search_fills(snap, "address", "805 E Patterson St")
    assert fills[0]["selector"] == "#AddrLookup"


def test_address_search_rejects_owner_armenia_placeholder():
    from app.browser.page_search_ai import _forbidden_input_for_query, build_search_fills

    owner_like = {
        "kind": "input",
        "selector": 'input[placeholder="Ex: ARMENIA"]',
        "placeholder": "Ex: ARMENIA",
        "label": "Owner Name:",
    }
    assert _forbidden_input_for_query(owner_like, "address")
    snap = {
        "elements": [
            owner_like,
            {
                "kind": "input",
                "selector": "#addr1",
                "placeholder": "Ex: 123 Main St",
                "label": "Address:",
                "id": "SiteStreetAddress",
            },
            {"kind": "search_button", "text": "Search", "selector": 'button:has-text("Search")'},
        ]
    }
    fills = build_search_fills(snap, "address", "805 E Patterson St")
    assert fills
    assert "armenia" not in fills[0]["selector"].lower()


def test_value_matches_parcel_before_submit():
    from app.browser.page_search_ai import PageSearchAI

    ps = PageSearchAI(db=None)
    q = "C-08-34-29-060-0000-2350"
    assert ps._value_matches_query(q, q, "parcel")
    assert ps._value_matches_query("C-08-34-29-060-0000-2350", q, "parcel")
    assert not ps._value_matches_query("", q, "parcel")
    assert not ps._value_matches_query("S-29-34-29-030-088A-0210", q, "parcel")


def test_submit_scoring_penalizes_header_go_button():
    from app.browser.page_search_ai import _score_submit_for_context

    go_btn = {
        "kind": "search_button",
        "tag": "button",
        "text": "Go",
        "selector": 'button:has-text("Go")',
        "layout_top": 40,
    }
    search_btn = {
        "kind": "search_button",
        "tag": "button",
        "text": "Search",
        "selector": 'button:has-text("Search")',
        "layout_top": 420,
    }
    primary = {
        "id": "RealEstateParcelId",
        "selector": "#RealEstateParcelId",
        "layout_top": 400,
        "panel_id": "parcelPanel",
    }
    assert _score_submit_for_context(search_btn, primary, "parcel") > _score_submit_for_context(
        go_btn, primary, "parcel"
    )


def test_pick_query_mode_tab_highlands_parcel():
    from app.browser.page_search_ai import _pick_query_mode_tab, refine_plan_from_snapshot

    snap = {
        "url": "https://www.hcpao.org/Search",
        "elements": [
            {"kind": "tab", "text": "Real Estate", "selector": "button:has-text('Real Estate')"},
            {"kind": "tab", "text": "Owner Name", "selector": "button:has-text('Owner Name')"},
            {"kind": "tab", "text": "Parcel ID", "selector": "button:has-text('Parcel ID')"},
            {"kind": "tab", "text": "Address", "selector": "button:has-text('Address')"},
            {
                "kind": "input",
                "id": "RealEstateParcelId",
                "selector": "#RealEstateParcelId",
                "label": "Parcel ID",
            },
            {
                "kind": "search_button",
                "id": "btnSearch",
                "selector": "button:has-text('Search')",
                "text": "Search",
            },
        ],
    }
    tab = _pick_query_mode_tab(snap["elements"], "parcel")
    assert tab is not None
    assert "parcel" in (tab.get("text") or "").lower()

    plan = refine_plan_from_snapshot(
        {
            "input_selector": "#RealEstateParcelId",
            "submit_selector": "button:has-text('Search')",
            "query_type": "parcel",
        },
        snap,
        "parcel",
        "C-08-34-29-060-0000-2350",
    )
    assert plan.get("tab_selector")
    assert "parcel" in plan["tab_selector"].lower()
    assert plan["fills"][0]["selector"] == "#RealEstateParcelId"


def test_combined_hero_search_picks_search_input_not_sidebar():
    """Assessor home pages: one box for owner/address/PCN + submit; no mode tab clicks."""
    from app.browser.page_search_ai import (
        _pick_query_mode_tab,
        build_search_fills,
        refine_plan_from_snapshot,
    )

    snap = {
        "url": "https://pbcpao.gov/index.htm",
        "elements": [
            {
                "kind": "input",
                "id": "searchInput",
                "selector": "#searchInput",
                "placeholder": "Search by Owner Name (Last Name first), Address, or PCN",
            },
            {
                "kind": "input",
                "id": "btnReal",
                "selector": "#btnReal",
                "type": "submit",
                "text": "Search",
            },
            {
                "kind": "search_button",
                "id": "btnReal",
                "selector": "#btnReal",
                "type": "submit",
                "text": "Search",
            },
            {
                "kind": "button",
                "text": "Mailing Address Change Form",
                "selector": 'a:has-text("Mailing Address Change Form")',
                "href": "/forms/address.pdf",
            },
            {
                "kind": "button",
                "id": "goog-gt-thumbUpButton",
                "selector": "#goog-gt-thumbUpButton",
                "text": "Good translation",
            },
        ],
    }
    assert _pick_query_mode_tab(snap["elements"], "address") is None
    fills = build_search_fills(snap, "address", "277 ONEIDA TER")
    assert fills[0]["selector"] == "#searchInput"

    plan = refine_plan_from_snapshot(
        {
            "input_selector": "#searchInput",
            "submit_selector": "#btnReal",
            "tab_selector": 'a:has-text("Address Change")',
            "query_type": "address",
        },
        snap,
        "address",
        "277 ONEIDA TER",
    )
    assert plan.get("tab_selector") is None
    assert plan["input_selector"] == "#searchInput"
    assert plan["submit_selector"] == "#btnReal"



