"""Tests for Tax Collector Automation, Modal Dismissal, and Document Capture."""
import uuid
import pytest
from app.extraction.tax_collector_reports import (
    is_tax_collector_portal,
    extract_tax_collector_record,
    _clean_storage_key,
    _tax_collector_detail_url,
    run_tax_collector_unified_search,
    ensure_tax_collector_search_page,
    record_has_tax_collector_identity,
    _unique_tax_years_sorted_desc,
    years_from_account_history,
    sanitize_tax_results_list_url,
    tax_search_result_link_matches_query,
    _dedupe_tax_search_targets_by_year,
    body_shows_visualgov_tax_year_table,
    tax_search_results_same_document_state,
)
from app.browser.page_search_ai import build_search_fills
from app.models.document_artifact import DocumentArtifact, DocumentStatus
from app.workflow_engine.execution_context import ExecutionContext
from app.workflow_engine.node_executor import TaxNodeExecutor, NormalizeNodeExecutor


def test_tax_collector_detail_url_detection():
    assert _tax_collector_detail_url(
        "https://example-taxcollector.com/Property/TaxBill#DelinquentHistory"
    )
    assert _tax_collector_detail_url("https://county-taxes.net/fl-example/property-tax/account/12345")
    assert not _tax_collector_detail_url(
        "https://example-taxcollector.com/Property/SearchSelect?Accept=true&ClearData=True"
    )
    assert not _tax_collector_detail_url("https://county-taxes.net/fl-example/property-tax")


def test_is_tax_collector_portal():
    assert is_tax_collector_portal("https://county-taxes.net/fl-miamidade/property-tax") is True
    assert is_tax_collector_portal("https://miamidade.county-taxes.com/govhub/property-tax/...") is True
    assert is_tax_collector_portal("https://broward.county-taxes.net") is True
    assert is_tax_collector_portal("https://floridatax.us/taxcollector") is True
    assert is_tax_collector_portal("https://www.miamidade.gov/Apps/PA/propertysearch") is False


def test_clean_storage_key():
    assert _clean_storage_key("30-4009-094-0050") == "30-4009-094-0050"
    assert _clean_storage_key("Miami-Dade/Tax 2026") == "Miami-Dade_Tax_2026"


def test_build_search_fills_county_taxes_unified_box():
    snap = {
        "url": "https://county-taxes.net/fl-example/property-tax",
        "title": "Property Tax",
        "elements": [
            {
                "kind": "input",
                "id": "accountSearch",
                "selector": "#accountSearch",
                "placeholder": "Name, Address, Account Number, Folio Number, etc.",
                "label": "Search",
            }
        ],
    }
    fills = build_search_fills(snap, "address", "9381 SW 21 TER")
    assert len(fills) == 1
    assert fills[0]["selector"] == "#accountSearch"
    assert fills[0]["value"] == "9381 SW 21 TER"


def test_tax_collector_search_helpers_exported():
    assert callable(run_tax_collector_unified_search)
    assert callable(ensure_tax_collector_search_page)


def test_record_has_tax_collector_identity():
    assert record_has_tax_collector_identity({"owner_name": "MARIA C MARRERO", "account_number": "30-4009-096-0060"})
    assert not record_has_tax_collector_identity({"owner_name": "OWNER OF RECORD"})
    assert not record_has_tax_collector_identity(None)


def test_tax_search_results_same_document_state():
    assert tax_search_results_same_document_state(
        "https://www.washingtoncountytaxcollector.com/Property/SearchSelect?Accept=true"
    )
    assert not tax_search_results_same_document_state(
        "https://county-taxes.net/fl-example/property-tax/account/123"
    )


def test_body_shows_visualgov_tax_year_table():
    parcel = "00000000-00-0001-0025"
    body = (
        "Search Selection\n14 records found\n"
        "TO VIEW OR PAY A TAX BILL CLICK THE PROPERTY NUMBER BELOW!\n"
        f"R - {parcel} - 2024\nR - {parcel} - 2025\n"
    )
    assert body_shows_visualgov_tax_year_table(body, parcel)
    assert not body_shows_visualgov_tax_year_table("Property Search form only", parcel)


def test_sanitize_tax_results_list_url():
    assert sanitize_tax_results_list_url(
        "https://www.washingtoncountytaxcollector.com/Property/Search#true#0"
    ) == "https://www.washingtoncountytaxcollector.com/Property/Search"
    assert sanitize_tax_results_list_url("https://example.com/list") == "https://example.com/list"


def test_tax_search_result_link_matches_washington_rows():
    parcel = "00000000-00-0001-0022"
    row = f"R - {parcel} - 2025"
    assert tax_search_result_link_matches_query(row, parcel)
    assert tax_search_result_link_matches_query(f"R - {parcel} - 2024", parcel)


def test_dedupe_tax_search_targets_picks_two_most_recent_years():
    rows = [
        {"year": "2025", "text": "R - x - 2025"},
        {"year": "2024", "text": "R - x - 2024"},
        {"year": "2024", "text": "R - x - 2024 dup"},
        {"year": "2023", "text": "R - x - 2023"},
    ]
    recent = _dedupe_tax_search_targets_by_year(rows)[:2]
    assert [r["year"] for r in recent] == ["2025", "2024"]


def test_tax_year_index_helpers():
    history = [
        {"year": "2024", "bill": "2024 Annual Bill"},
        {"year": "2026", "bill": "2026 Annual Bill"},
        {"year": "2025", "bill": "2025 Annual Bill"},
        {"year": "2026", "bill": "2026 Annual Bill duplicate"},
    ]
    assert years_from_account_history(history) == ["2026", "2025", "2024"]
    assert _unique_tax_years_sorted_desc(["2019", "2021", "2020", "2021"]) == ["2021", "2020", "2019"]


@pytest.mark.asyncio
async def test_tax_node_mock_fallback():
    executor = TaxNodeExecutor()
    run_id = uuid.uuid4()
    context = ExecutionContext(
        run_id=run_id,
        workflow_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        workflow_definition={"nodes": []},
        initial_input={"parcel_id": "30-4009-094-0050", "force_mock": True},
    )
    node = {
        "id": "node-tax",
        "data": {
            "record_type": "tax",
        },
    }
    result = await executor.execute(node, context)
    assert result.status == "COMPLETED"
    assert "tax" in result.output_data
    assert result.output_data["tax"]["parcel_number"] == "30-4009-094-0050"


@pytest.mark.asyncio
async def test_normalize_with_tax_only_branch():
    norm_executor = NormalizeNodeExecutor()
    run_id = uuid.uuid4()
    context = ExecutionContext(
        run_id=run_id,
        workflow_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        workflow_definition={
            "nodes": [
                {"id": "node-tax", "type": "tax"},
                {"id": "node-normalize", "type": "normalize"},
            ],
            "edges": [
                {"source": "node-tax", "target": "node-normalize"},
            ],
        },
        initial_input={"parcel_id": "30-4009-094-0050"},
    )
    # Set tax output directly into context
    context.node_outputs["node-tax"] = {
        "tax": {
            "parcel_number": "30-4009-094-0050",
            "account_number": "30-4009-094-0050",
            "owner_name": "LUCIO RAINELLI &W MARGARITA R",
            "property_address": "9441 SW 21 ST, Unincorporated County 33165-0000",
            "status": "Unpaid",
            "amount_due": 4378.76,
            "total_amount_due": 4378.76,
            "is_delinquent": False,
        }
    }
    node = {
        "id": "node-normalize",
        "data": {},
    }
    result = await norm_executor.execute(node, context)
    assert result.status == "COMPLETED"
    normalized = result.output_data["normalized"]
    assert normalized["parcel_id"] == "30-4009-094-0050"
    assert normalized["owner_name"] == "LUCIO RAINELLI &W MARGARITA R"
    assert normalized["property_address"] == "9441 SW 21 ST, Unincorporated County 33165-0000"
    assert normalized["tax_status"] == "Unpaid"
    assert normalized["tax_amount_due"] == 4378.76
