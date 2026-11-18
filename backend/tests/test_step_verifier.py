from app.browser.step_verifier_ai import _heuristic_verify


def test_detects_still_on_search_results():
    body = "Search Results Parcel ID Owners Name 21-37-25-0000-0040-0000 CISNEROS"
    r = _heuristic_verify(
        "https://www.desotopa.com/gis/",
        body,
        action="click_if_visible",
        step_desc="Opened property appraisal from results row",
        query_type="parcel",
        query_value="21-37-25-0000-0040-0000",
    )
    assert r["recovery"] == "open_gis_search_result"
    assert not r["ok"]


def test_detects_certified_values_noise():
    body = "DeSoto County Property Appraiser 2026 Certified Values David"
    r = _heuristic_verify(
        "https://www.desotopa.com/gis/",
        body,
        action="click_if_visible",
        step_desc="click result",
        query_type="parcel",
        query_value="21-37-25-0000-0040-0000",
    )
    assert not r["ok"]
