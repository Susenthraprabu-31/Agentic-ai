import pytest
from app.extraction.florida_extractors import parse_dynamic_full_page_record

def test_parse_dynamic_full_page_record_handles_knockout_and_hillsborough():
    data = {
        "key_values": {
            "owner name": "CARPENTER WILLIE THOMAS ESTATE OF",
            "site address": "805 E PATTERSON ST, TAMPA",
            "mailing address": "805 E PATTERSON ST TAMPA, FL 33604-4222",
            "folio": "161425-0000",
            "pin": "A-25-28-18-4DX-000000-00053.0",
            "market value": "$202,063",
            "assessed value": "$202,063",
            "taxable value": "$202,063",
            "property use": "0100 SINGLE FAMILY R",
            "year built": "1947",
            "subdivision": "4DX | WARNER SUBDIVISION",
            "1": "WARNER SUBDIVISION LOT 53 AND N 1/2 OF CLOSED ALLEY ABUTTING ON S",
            "address": "OR",
        },
        "sales_history": [
            {"date": "12/1/1995", "price": "$100", "type": "WD"}
        ],
        "assessment_history": [],
        "buildings": [
            {"type": "SINGLE FAMILY", "year_built": "1947"}
        ],
        "land_lines": [
            {"type": "RES", "acres": "0.14"}
        ],
        "extra_features": [],
        "other_tables": [],
        "page_title": "Hillsborough County Property Appraiser",
        "page_url": "https://gis.hcpafl.org/PropertySearch/#/parcel/basic/1828254DX000000000530A"
    }

    rec = parse_dynamic_full_page_record(data, source_url=data["page_url"], query_value="161425-0000")

    assert rec["owner_name"] == "CARPENTER WILLIE THOMAS ESTATE OF"
    assert rec["property_address"] == "805 E PATTERSON ST, TAMPA"
    assert rec["parcel_number"] == "161425-0000"
    assert rec["assessed_value"] == 202063.0
    assert rec["just_value"] == 202063.0
    assert "WARNER SUBDIVISION" in rec["legal_description"]
    assert rec["property_use"] == "0100 SINGLE FAMILY R"
    assert len(rec["sales_history"]) == 1
    assert len(rec["buildings"]) == 1
    assert len(rec["land_lines"]) == 1


def test_is_genuine_property_identity_rejects_dummy_search_page_strings():
    from app.workflow_engine.node_executor import _is_genuine_property_identity

    # Dummy search page extract that previously fooled node_executor
    dummy_data = {
        "owner_name": "OWNER OF RECORD",
        "parcel_number": "(Only available with Folio or Parcel Number Search)",
        "property_address": "OR",
    }
    search_url = "https://gis.hcpafl.org/PropertySearch/#/nav/Basic%20Search"
    assert not _is_genuine_property_identity(dummy_data, current_url=search_url)

    # Empty or partial dummy values
    assert not _is_genuine_property_identity({}, current_url=search_url)
    assert not _is_genuine_property_identity({"owner_name": "UNKNOWN"}, current_url=search_url)
    assert not _is_genuine_property_identity({"owner_name": "N/A", "property_address": "AND"}, current_url=search_url)


def test_is_genuine_property_identity_accepts_authentic_property_records():
    from app.workflow_engine.node_executor import _is_genuine_property_identity

    # Genuine record on detail page
    genuine_data = {
        "owner_name": "CARPENTER WILLIE THOMAS ESTATE OF",
        "parcel_number": "161425-0000",
        "property_address": "805 E PATTERSON ST, TAMPA",
    }
    detail_url = "https://gis.hcpafl.org/PropertySearch/#/parcel/basic/1828254DX000000000530A"
    assert _is_genuine_property_identity(genuine_data, current_url=detail_url)

    # Genuine record with owner and address
    assert _is_genuine_property_identity({
        "owner_name": "JUAN A MORALES",
        "property_address": "24324 SW 118 CT, HOMESTEAD, FL 33032",
        "parcel_number": "30-6924-001-0030"
    }, current_url="https://county-taxes.net/fl-miamidade/property-tax")


