"""Common FastAPI dependencies (authentication, DB session, permissions)."""

import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User

_http_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_http_bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Validate Bearer access token and return the authenticated user."""
    if credentials is None or not credentials.credentials:
        raise AuthenticationError(
            message="Authentication credentials were not provided",
            code="UNAUTHENTICATED",
        )

    token = credentials.credentials
    payload = decode_access_token(token)

    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, TypeError, KeyError) as exc:
        raise AuthenticationError(
            message="Invalid user identifier in token",
            code="INVALID_TOKEN_SUBJECT",
        ) from exc

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise AuthenticationError(
            message="User account no longer exists",
            code="USER_NOT_FOUND",
        )

    if not user.is_active:
        raise AuthenticationError(
            message="User account is deactivated",
            code="USER_INACTIVE",
        )

    return user


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Ensure the user is active."""
    if not current_user.is_active:
        raise AuthenticationError(
            message="User account is inactive",
            code="USER_INACTIVE",
        )
    return current_user


async def get_current_superuser(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    """Ensure the user has superuser privileges."""
    if not current_user.is_superuser:
        raise AuthorizationError(
            message="Superuser privileges required for this action",
            code="FORBIDDEN",
        )
    return current_user

