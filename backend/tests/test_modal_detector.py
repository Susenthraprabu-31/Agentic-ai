"""Tests for AI and fast-path modal disclaimer detection and recipe synthesis."""

import pytest
from app.services.ai_page_analyzer import AIPageAnalyzerService


def test_synthesize_recipe_with_disclaimer_action():
    analyzer = AIPageAnalyzerService(db=None)

    dom = {
        "disclaimer_action": {
            "has_disclaimer": True,
            "dialog_title": "DeSoto County Property Appraiser - Public Access",
            "dismiss_button_text": "I agree, please continue",
            "dismiss_selector": "#button1",
            "description": "Dismiss 'DeSoto County Property Appraiser - Public Access' modal with 'I agree, please continue'",
        },
        "has_disclaimer": True,
        "tabs": [],
        "inputs": [
            {"id": "ParcelID", "type": "text", "placeholder": "Enter Parcel ID", "selector": "#ParcelID"}
        ],
        "buttons": [
            {"id": "submit_RecordSearch", "text": "Run Search >>", "selector": "#submit_RecordSearch"}
        ],
    }

    recipe = analyzer._synthesize_recipe_definition(
        dom=dom,
        portal_url="https://desotopa.com/gis/",
        county_name="DeSoto",
        record_type="assessor",
    )

    steps = recipe["steps"]
    assert len(steps) >= 2
    assert steps[0]["action"] == "navigate"
    assert steps[1]["action"] == "click_if_visible"
    assert "#button1" in steps[1]["selector"]
    assert "input[value*='agree' i]" in steps[1]["selector"]
    assert steps[1]["optional"] is True


def test_synthesize_recipe_without_disclaimer():
    analyzer = AIPageAnalyzerService(db=None)

    dom = {
        "has_disclaimer": False,
        "tabs": [],
        "inputs": [
            {"id": "ParcelID", "type": "text", "placeholder": "Enter Parcel ID", "selector": "#ParcelID"}
        ],
        "buttons": [
            {"id": "btnSearch", "text": "Search", "selector": "#btnSearch"}
        ],
    }

    recipe = analyzer._synthesize_recipe_definition(
        dom=dom,
        portal_url="https://example.gov/search",
        county_name="Example",
        record_type="assessor",
    )

    steps = recipe["steps"]
    assert steps[0]["action"] == "navigate"
    # No click_if_visible for disclaimer should precede form fill
    actions = [s["action"] for s in steps]
    assert actions[0] == "navigate"
    assert actions[1] != "click_if_visible"

