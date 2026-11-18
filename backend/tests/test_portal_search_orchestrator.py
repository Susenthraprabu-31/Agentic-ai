"""Tests for first-time AI vs saved recipe orchestration."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.browser.recipe_loader import ResolvedRecipe
from app.services.portal_search_orchestrator import (
    ExecutionMode,
    assess_recipe_compatibility,
    classify_portal_type,
    decide_execution_mode,
    is_genuine_property_identity,
    validate_property_matches_query,
    RecipeCompatibilityResult,
    PropertyValidationResult,
)


def test_classify_portal_types():
    assert classify_portal_type("https://qpublic.schneidercorp.com/Application.aspx") == "qpublic_schneider"
    assert classify_portal_type("https://columbia.floridapa.com/gis/") == "gis_map"
    assert classify_portal_type("https://www.citruspa.org/_Web/search/CommonSearch.aspx") == "commonsearch"


def test_decide_first_time_no_recipe():
    mode, reason = decide_execution_mode(
        resolved=None,
        compat=RecipeCompatibilityResult(False, ["no_recipe_steps"]),
        recipe_executed=False,
        recipe_execution_success=False,
        property_validation=PropertyValidationResult(False, False, False, ["no_data"]),
    )
    assert mode == ExecutionMode.FIRST_TIME_AI_DISCOVERY


def test_decide_saved_recipe_when_valid():
    resolved = ResolvedRecipe(
        recipe_id=uuid.uuid4(),
        recipe_version_id=uuid.uuid4(),
        recipe_name="Test",
        record_type="assessor",
        website_url="https://qpublic.schneidercorp.com/x",
        search_page_url="https://qpublic.schneidercorp.com/x",
        search_method="FORM_SUBMIT",
        steps=[{"action": "fill"}],
        is_published=True,
    )
    prop = PropertyValidationResult(True, True, True, [])
    mode, _ = decide_execution_mode(
        resolved=resolved,
        compat=RecipeCompatibilityResult(True),
        recipe_executed=True,
        recipe_execution_success=True,
        property_validation=prop,
    )
    assert mode == ExecutionMode.SAVED_RECIPE_EXECUTION


def test_decide_ai_recovery_on_county_mismatch_compat():
    mode, reason = decide_execution_mode(
        resolved=ResolvedRecipe(
            recipe_id=uuid.uuid4(),
            recipe_version_id=uuid.uuid4(),
            recipe_name="Clay",
            record_type="assessor",
            website_url="https://qpublic.schneidercorp.com/",
            search_page_url="https://qpublic.schneidercorp.com/",
            search_method="FORM_SUBMIT",
            steps=[{}],
        ),
        compat=RecipeCompatibilityResult(False, ["county_source_mismatch"]),
        recipe_executed=False,
        recipe_execution_success=False,
        property_validation=PropertyValidationResult(False, False, False, []),
    )
    assert mode == ExecutionMode.AI_RECOVERY
    assert "county_source_mismatch" in reason


def test_property_validation_address_match():
    ok = validate_property_matches_query(
        {"owner_name": "DOE", "property_address": "4574 ALAN LAKE RD", "parcel_number": "09-08-24-006919"},
        "address",
        "4574 ALAN LAKE Rd",
    )
    assert ok.valid
    assert ok.match_ok


def test_property_validation_address_mismatch():
    bad = validate_property_matches_query(
        {"owner_name": "DOE", "property_address": "999 OTHER ST", "parcel_number": "X"},
        "address",
        "4574 ALAN LAKE Rd",
    )
    assert not bad.valid
    assert "address_query_token_mismatch" in bad.reasons


def test_genuine_identity_rejects_gis_hub_without_detail():
    data = {"owner_name": "DOE", "parcel_number": "07-4S-17", "property_address": "123 MAIN"}
    assert is_genuine_property_identity(data, "https://columbia.floridapa.com/gis/") is False
    assert is_genuine_property_identity(
        data,
        "https://columbia.floridapa.com/gis/?PageTypeID=4&KeyValue=123",
    )


@pytest.mark.asyncio
async def test_recipe_incompatible_cross_county_hostname():
    clay_id = uuid.uuid4()
    columbia_id = uuid.uuid4()
    page = MagicMock()
    page.url = "https://columbia.floridapa.com/gis/"
    page.frames = []
    page.locator = MagicMock(return_value=AsyncMock(count=AsyncMock(return_value=1)))

    resolved = ResolvedRecipe(
        recipe_id=uuid.uuid4(),
        recipe_version_id=uuid.uuid4(),
        recipe_name="Clay County Assessor",
        record_type="assessor",
        website_url="https://qpublic.schneidercorp.com/Application.aspx?AppID=830",
        search_page_url="https://qpublic.schneidercorp.com/Application.aspx?AppID=830",
        search_method="FORM_SUBMIT",
        steps=[
            {"action": "navigate", "url": "https://qpublic.schneidercorp.com/Application.aspx?AppID=830"},
            {"action": "fill", "selector": "#addr", "condition": "input.address"},
        ],
        county_source_id=clay_id,
        portal_hostname="qpublic.schneidercorp.com",
        portal_type="qpublic_schneider",
        is_published=True,
        definition={
            "metadata": {
                "portal_hostname": "qpublic.schneidercorp.com",
                "portal_type": "qpublic_schneider",
                "county_source_id": str(clay_id),
            }
        },
    )

    result = await assess_recipe_compatibility(
        page,
        resolved,
        portal_url="https://columbia.floridapa.com/gis/",
        county_source_id=str(columbia_id),
        query_type="address",
    )
    assert not result.compatible
    assert any("county_source_mismatch" in r for r in result.reasons)
    assert any("hostname_mismatch" in r or "portal_type_mismatch" in r for r in result.reasons)
