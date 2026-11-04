"""Basic tests for the FastAPI application."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_liveness() -> None:
    """GET /api/v1/health/live must return HTTP 200 with status 'ok'."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_root_redirects_to_health() -> None:
    """GET / must redirect to /api/v1/health."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        response = await client.get("/")
    assert response.status_code in (301, 302, 307, 308)
    assert "/api/v1/health" in response.headers["location"]


@pytest.mark.asyncio
async def test_not_implemented_returns_501() -> None:
    """Stub routes must return HTTP 501 Not Implemented."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/results/assessor")
    assert response.status_code == 501


@pytest.mark.asyncio
async def test_full_health_endpoint() -> None:
    """GET /api/v1/health must return HTTP 200 with structured status information."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "version" in data
    assert "database" in data
    assert "redis" in data


@pytest.mark.asyncio
async def test_readiness_endpoint() -> None:
    """GET /api/v1/health/ready must return HTTP 200."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
