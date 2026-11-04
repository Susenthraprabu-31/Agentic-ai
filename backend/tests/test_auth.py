"""Unit and integration tests for Phase 2A Authentication System."""

import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import User, RefreshToken  # noqa: F401


# ---------------------------------------------------------------------------
# In-Memory Test Database Fixtures (Isolated for unit/integration testing)
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


AUTH_TABLES = [User.__table__, RefreshToken.__table__]


@pytest_asyncio.fixture(scope="function", autouse=True)
async def setup_test_db() -> AsyncGenerator[None, None]:
    """Create all auth tables before each test and drop them after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=AUTH_TABLES))
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=AUTH_TABLES))


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI DB dependency override for tests."""
    async with TestSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """HTTP client wired with the test DB dependency."""
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Security & Password Unit Tests
# ---------------------------------------------------------------------------


def test_password_hashing() -> None:
    raw = "SecureP@ssw0rd123"
    hashed = hash_password(raw)
    assert hashed != raw
    assert verify_password(raw, hashed) is True
    assert verify_password("WrongP@ssw0rd", hashed) is False


def test_jwt_access_token_creation_and_decoding() -> None:
    user_id = str(uuid.uuid4())
    token = create_access_token(subject=user_id)
    payload = decode_access_token(token)
    assert payload["sub"] == user_id
    assert payload["type"] == "access"
    assert "exp" in payload


def test_jwt_refresh_token_creation_and_decoding() -> None:
    user_id = str(uuid.uuid4())
    token, jti, expires_at = create_refresh_token(subject=user_id)
    payload = decode_refresh_token(token)
    assert payload["sub"] == user_id
    assert payload["type"] == "refresh"
    assert payload["jti"] == jti
    assert expires_at is not None


# ---------------------------------------------------------------------------
# API Integration Tests: Registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_success(client: AsyncClient) -> None:
    payload = {
        "email": "investor@example.com",
        "password": "SecurePassword123",
        "full_name": "John Doe",
    }
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "investor@example.com"
    assert data["full_name"] == "John Doe"
    assert "id" in data
    assert "hashed_password" not in data
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_register_duplicate_email_rejected(client: AsyncClient) -> None:
    payload = {
        "email": "duplicate@example.com",
        "password": "SecurePassword123",
        "full_name": "First User",
    }
    res1 = await client.post("/api/v1/auth/register", json=payload)
    assert res1.status_code == 201

    # Attempt to register again with same email (different case)
    res2 = await client.post(
        "/api/v1/auth/register",
        json={"email": "DUPLICATE@example.com", "password": "AnotherPassword456"},
    )
    assert res2.status_code == 409
    assert res2.json()["code"] == "EMAIL_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_register_weak_password_rejected(client: AsyncClient) -> None:
    # Too short (< 8 chars)
    res = await client.post(
        "/api/v1/auth/register",
        json={"email": "short@example.com", "password": "pass1"},
    )
    assert res.status_code == 422

    # Missing number
    res2 = await client.post(
        "/api/v1/auth/register",
        json={"email": "nonum@example.com", "password": "passwordonly"},
    )
    assert res2.status_code == 422


# ---------------------------------------------------------------------------
# API Integration Tests: Login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient) -> None:
    # 1. Register
    await client.post(
        "/api/v1/auth/register",
        json={"email": "loginuser@example.com", "password": "ValidPassword99"},
    )

    # 2. Login
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "loginuser@example.com", "password": "ValidPassword99"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "loginuser@example.com"

    # Verify HttpOnly refresh cookie is set
    cookie_header = res.headers.get("set-cookie")
    assert cookie_header is not None
    assert settings.REFRESH_COOKIE_NAME in cookie_header
    assert "HttpOnly" in cookie_header


@pytest.mark.asyncio
async def test_login_invalid_password(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"email": "wrongpwd@example.com", "password": "ValidPassword99"},
    )
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "wrongpwd@example.com", "password": "IncorrectPassword1"},
    )
    assert res.status_code == 401
    assert res.json()["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_login_nonexistent_user(client: AsyncClient) -> None:
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "ghost@example.com", "password": "ValidPassword99"},
    )
    assert res.status_code == 401
    assert res.json()["code"] == "INVALID_CREDENTIALS"


# ---------------------------------------------------------------------------
# API Integration Tests: /auth/me (Protected Route)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_me_authenticated(client: AsyncClient) -> None:
    # Register & Login
    await client.post(
        "/api/v1/auth/register",
        json={"email": "me@example.com", "password": "ValidPassword99", "full_name": "Test Subject"},
    )
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": "me@example.com", "password": "ValidPassword99"},
    )
    token = login_res.json()["access_token"]

    # Call /auth/me with Bearer token
    res = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    profile = res.json()
    assert profile["email"] == "me@example.com"
    assert profile["full_name"] == "Test Subject"


@pytest.mark.asyncio
async def test_get_me_unauthenticated_rejected(client: AsyncClient) -> None:
    # No auth header
    res = await client.get("/api/v1/auth/me")
    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHENTICATED"

    # Invalid token
    res2 = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert res2.status_code == 401


# ---------------------------------------------------------------------------
# API Integration Tests: Token Refresh, Rotation & Revocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_token_rotation_and_revocation(client: AsyncClient) -> None:
    # 1. Register and login
    await client.post(
        "/api/v1/auth/register",
        json={"email": "rotator@example.com", "password": "ValidPassword99"},
    )
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": "rotator@example.com", "password": "ValidPassword99"},
    )
    first_refresh_token = login_res.json()["refresh_token"]

    # 2. Refresh with first token
    refresh_res = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first_refresh_token},
    )
    assert refresh_res.status_code == 200
    new_data = refresh_res.json()
    second_refresh_token = new_data["refresh_token"]
    assert second_refresh_token != first_refresh_token

    # 3. Attempt to RE-USE the first refresh token (should be revoked)
    reuse_res = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first_refresh_token},
    )
    assert reuse_res.status_code == 401
    assert reuse_res.json()["code"] == "TOKEN_REVOKED"

    # 4. Using the second rotated token should succeed
    second_refresh_res = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": second_refresh_token},
    )
    assert second_refresh_res.status_code == 200


# ---------------------------------------------------------------------------
# API Integration Tests: Logout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_revokes_token(client: AsyncClient) -> None:
    # 1. Register & login
    await client.post(
        "/api/v1/auth/register",
        json={"email": "logoutuser@example.com", "password": "ValidPassword99"},
    )
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": "logoutuser@example.com", "password": "ValidPassword99"},
    )
    refresh_token = login_res.json()["refresh_token"]

    # 2. Logout with token
    logout_res = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refresh_token},
    )
    assert logout_res.status_code == 204

    # 3. Attempting to refresh with the logged out token must fail
    refresh_after_logout = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_after_logout.status_code == 401
    assert refresh_after_logout.json()["code"] == "TOKEN_REVOKED"
