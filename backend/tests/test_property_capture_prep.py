"""Tests for property capture prep helpers."""

from app.browser.property_capture_prep import (
    body_likely_property_detail,
    detail_url_from_print_route,
    is_map_ui_noise,
    is_plausible_parcel_id,
    is_print_route_url,
    is_standalone_parcel_detail_url,
    panel_has_property_data,
    sanitize_extracted_record,
    _href_looks_like_print_route,
)


def test_rejects_map_toolbar_as_address():
    assert is_map_ui_noise(
        "Show Scalebar Auto hide PRINT panel Show Zoom IN/OUT menu icons Auto-track GPS location"
    )


def test_rejects_str_grid_as_parcel():
    assert not is_plausible_parcel_id("S T R Grid", "09-35-26-00005-009-19XX")
    assert is_plausible_parcel_id("09-35-26-00005-009-19", "09-35-26-00005-009-19XX")


def test_detail_url_from_print_route():
    assert detail_url_from_print_route(
        "https://search.ncpafl.com/parcel/27%20N%2035/print"
    ) == "https://search.ncpafl.com/parcel/27%20N%2035"


def test_body_likely_property_detail_accepts_parcel_match():
    text = "Owner Name: RAYONIER\nParcel: 50-3N-27-0000-0001-0280\nAssessed Value: $100"
    assert body_likely_property_detail(text, "50-3N-27-0000-0001-0280")


def test_print_route_url_detection():
    assert is_print_route_url("https://search.ncpafl.com/parcel/27%20N%2035/print")
    assert not is_print_route_url("https://search.ncpafl.com/search/p/")
    assert _href_looks_like_print_route("/parcel/123/print")


def test_standalone_parcel_detail_url():
    assert is_standalone_parcel_detail_url(
        "https://search.ncpafl.com/parcel/27%20N%2020/2130720001370000"
    )
    assert not is_standalone_parcel_detail_url(
        "https://search.ncpafl.com/parcel/27%20N%2035/print"
    )
    assert not is_standalone_parcel_detail_url(
        "https://www.floridapa.com/gis.html?PageTypeID=4&KeyValue=123"
    )
    assert is_standalone_parcel_detail_url(
        "https://pbcpao.gov/Property/Details?parcelId=73414404010230190"
    )
    assert not is_standalone_parcel_detail_url(
        "https://pbcpao.gov/MasterSearch/SearchResults?propertyType=RE&searchvalue=13763%20EXOTICA%20LN"
    )


def test_panel_has_property_data():
    assert not panel_has_property_data("Record Search   Parcel Details   GIS Map")
    assert panel_has_property_data("Owner Name: SMITH JOHN\nSitus: 123 MAIN ST\nJust Value: $100,000")


def test_pdf_rejects_portal_input_error():
    from app.browser.property_capture_prep import pdf_bytes_looks_valid

    bad = b"%PDF-1.4\nInput Error! Please Correct your Search Criteria"
    assert not pdf_bytes_looks_valid(bad, "21-37-25-0000-0010-0000")


def test_sanitize_drops_bad_parcel_and_map_address():
    rec = sanitize_extracted_record(
        {
            "parcel_number": "STR Grid",
            "property_address": "Show Scalebar Auto hide PRINT panel",
            "taxable_value": "703,484,703,484,783,500",
            "owner_name": "TEST OWNER",
        },
        query_value="09-35-26-00005-009-19",
        query_type="parcel",
    )
    assert rec["parcel_number"] == "09-35-26-00005-009-19"
    assert rec.get("property_address") is None
    assert rec["taxable_value"] == 703484.0
