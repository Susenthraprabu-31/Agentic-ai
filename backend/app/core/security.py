"""JWT and password utilities."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings
from app.core.exceptions import AuthenticationError

_ACCESS_TOKEN_TYPE = "access"
_REFRESH_TOKEN_TYPE = "refresh"


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Return bcrypt hash of *password*."""
    # Truncate to 72 bytes if necessary per bcrypt specification
    pw_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pw_bytes, salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*."""
    try:
        pw_bytes = plain.encode("utf-8")[:72]
        hashed_bytes = hashed.encode("utf-8")
        return bcrypt.checkpw(pw_bytes, hashed_bytes)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def _build_token(
    subject: str,
    token_type: str,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(
    subject: str,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create a signed JWT access token for *subject* (typically user UUID string)."""
    delta = expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return _build_token(subject, _ACCESS_TOKEN_TYPE, delta, extra_claims=extra_claims)


def create_refresh_token(
    subject: str,
    jti: str | None = None,
) -> tuple[str, str, datetime]:
    """Create a signed JWT refresh token for *subject*.

    Returns a tuple of (token_string, jti, expires_at_datetime).
    """
    token_jti = jti or str(uuid.uuid4())
    delta = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    expires_at = datetime.now(timezone.utc) + delta
    token = _build_token(
        subject,
        _REFRESH_TOKEN_TYPE,
        delta,
        extra_claims={"jti": token_jti},
    )
    return token, token_jti, expires_at


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify *token*. Raises :exc:`AuthenticationError` on failure."""
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except JWTError as exc:
        raise AuthenticationError(message=f"Invalid or expired token: {exc}", code="INVALID_TOKEN") from exc


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and verify an access token."""
    payload = decode_token(token)
    if payload.get("type") != _ACCESS_TOKEN_TYPE:
        raise AuthenticationError(message="Invalid token type, expected access token", code="INVALID_TOKEN_TYPE")
    if not payload.get("sub"):
        raise AuthenticationError(message="Token missing subject claim", code="INVALID_TOKEN_SUBJECT")
    return payload


def decode_refresh_token(token: str) -> dict[str, Any]:
    """Decode and verify a refresh token."""
    payload = decode_token(token)
    if payload.get("type") != _REFRESH_TOKEN_TYPE:
        raise AuthenticationError(message="Invalid token type, expected refresh token", code="INVALID_TOKEN_TYPE")
    if not payload.get("sub") or not payload.get("jti"):
        raise AuthenticationError(message="Refresh token missing required claims", code="INVALID_TOKEN_CLAIMS")
    return payload
