"""Tests for Tax Collector Automation, Modal Dismissal, and Document Capture."""
import uuid
import pytest
from app.extraction.tax_collector_reports import (
    is_tax_collector_portal,
    extract_tax_collector_record,
    _clean_storage_key,
)
from app.models.document_artifact import DocumentArtifact, DocumentStatus
from app.workflow_engine.execution_context import ExecutionContext
from app.workflow_engine.node_executor import TaxNodeExecutor, NormalizeNodeExecutor


def test_is_tax_collector_portal():
    assert is_tax_collector_portal("https://county-taxes.net/fl-miamidade/property-tax") is True
    assert is_tax_collector_portal("https://miamidade.county-taxes.com/govhub/property-tax/...") is True
    assert is_tax_collector_portal("https://broward.county-taxes.net") is True
    assert is_tax_collector_portal("https://floridatax.us/taxcollector") is True
    assert is_tax_collector_portal("https://www.miamidade.gov/Apps/PA/propertysearch") is False


def test_clean_storage_key():
    assert _clean_storage_key("30-4009-094-0050") == "30-4009-094-0050"
    assert _clean_storage_key("Miami-Dade/Tax 2026") == "Miami-Dade_Tax_2026"


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
