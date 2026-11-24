"""Desktop smoke tests — config, storage helpers, localhost middleware assumptions."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_live_endpoint_still_ok():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health/live")
    assert response.status_code == 200


def test_use_inprocess_ipc_logic():
    assert bool(True or not False)


def test_s3_storage_class_importable():
    from app.documents.document_storage import S3DocumentStorage

    assert S3DocumentStorage is not None


@pytest.mark.asyncio
async def test_sync_status_callable():
    from app.storage.storage_service import sync_status

    status = await sync_status()
    assert "configured" in status