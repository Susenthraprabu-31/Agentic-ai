"""Unit tests for Mistral Document AI OCR Webpage Extraction."""
import pytest
from app.extraction.mistral_webpage_ocr import (
    MistralWebpageOCRService,
    clean_text,
    parse_numeric_value,
)


def test_clean_text():
    assert clean_text("**OWNER:**") == "OWNER:"
    assert clean_text("  *Some   Text*  ") == "Some Text"
    assert clean_text(None) == ""


def test_parse_numeric_value():
    assert parse_numeric_value("$123,456.78") == 123456.78
    assert parse_numeric_value("100.50") == 100.50
    assert parse_numeric_value("N/A") is None
    assert parse_numeric_value("") is None


def test_parse_baker_county_markdown():
    ocr = MistralWebpageOCRService()
    sample_md = """
# BAKER COUNTY PROPERTY APPRAISER

# PARCEL DETAILS

PARCEL ID: 282S22021600001250

# OWNER & PROPERTY INFORMATION

|  OWNER: | O'KEEFE MORGAN  |
| --- | --- |
|  MAILING ADDRESS: | 575 ISLAMORADA DR N MACCLENNY, FL 32063  |
|  SITE ADDRESS: | 575 ISLAMORADA DR MACCLENNY, FL  |
|  PROPERTY USE: | 0100 - SINGLE FAMILY  |
|  TAXING DISTRICT: | MC  |
|  LAND SIZE(AC OR LOT): | 1.00  |
|  EXEMPTIONS: | HOMESTEAD, ADDITIONAL 25000 HX  |
|  MAP NUMBER: | 141B  |
"""

    record = ocr.parse_ocr_markdown(sample_md)

    assert record["parcel_number"] == "282S22021600001250"
    assert record["owner_name"] == "O'KEEFE MORGAN"
    assert record["property_address"] == "575 ISLAMORADA DR MACCLENNY, FL"
    assert "575 ISLAMORADA DR N" in record["mailing_address"]
    assert record["property_use"] == "0100 - SINGLE FAMILY"
    assert record["taxing_district"] == "MC"
    assert record["land_size"] == "1.00"
    assert "HOMESTEAD" in record["exemptions"]
    assert record["map_number"] == "141B"


def test_parse_valuation_and_sales_tables():
    ocr = MistralWebpageOCRService()
    sample_md = """
# PROPERTY SUMMARY

PARCEL ID: 12-34-56-789
OWNER NAME: DOE JOHN & JANE
SITE ADDRESS: 123 MAIN ST, MIAMI, FL

# VALUATION HISTORY

| Year | Just Value | Assessed Value | Taxable Value |
| --- | --- | --- | --- |
| 2024 | $350,000 | $280,000 | $230,000 |
| 2023 | $320,000 | $260,000 | $210,000 |

# SALES HISTORY

| Sale Date | Price | Book/Page | Grantor | Grantee |
| --- | --- | --- | --- | --- |
| 05/12/2021 | $420,000 | 1234/567 | SMITH ALICE | DOE JOHN |
"""

    record = ocr.parse_ocr_markdown(sample_md)

    assert record["parcel_number"] == "12-34-56-789"
    assert record["owner_name"] == "DOE JOHN & JANE"
    assert record["property_address"] == "123 MAIN ST, MIAMI, FL"
    assert record["just_value"] == 350000.0
    assert record["assessed_value"] == 280000.0
    assert len(record["valuation_history"]) == 2
    assert record["valuation_history"][0]["year"] == "2024"
    assert len(record["sales_history"]) == 1
    assert record["sales_history"][0]["grantee"] == "DOE JOHN"


def test_disabled_ocr_handling():
    ocr = MistralWebpageOCRService()
    ocr.settings.MISTRAL_OCR_ENABLED = False
    assert ocr.is_enabled() is False

