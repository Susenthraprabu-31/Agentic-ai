from app.browser.property_capture_prep import finalize_assessor_extraction


def test_collapses_empty_grid_rows_uses_top_level_ocr():
    extracted = {
        "owner_name": "BURNS EUGENE W",
        "property_address": "123 MAIN ST",
        "parcel_number": "21-37-23-0000-0010-0000",
        "assessed_value": 150000,
        "_records": [{}, {}] + [{"owner_name": None} for _ in range(23)],
    }
    out = finalize_assessor_extraction(
        extracted,
        query_value="21-37-23-0000-0010-0000",
        query_type="parcel",
    )
    assert len(out["_records"]) == 1
    assert out["_records"][0]["owner_name"] == "BURNS EUGENE W"
    assert out["owner_name"] == "BURNS EUGENE W"
