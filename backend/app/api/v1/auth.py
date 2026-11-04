"""Authentication router — register, login, refresh, logout, and me."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.core.config import settings
from app.core.exceptions import AuthenticationError
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import LoginRequest, RefreshRequest, TokenResponse, UserCreateRequest, UserResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """Helper to attach an HttpOnly, secure refresh token cookie."""
    max_age_seconds = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=max_age_seconds,
        httponly=True,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
        path=settings.REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    """Helper to delete the refresh token cookie."""
    response.delete_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        path=settings.REFRESH_COOKIE_PATH,
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def register(
    payload: UserCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    """Register a new platform user with name, email and password."""
    service = AuthService(db)
    return await service.register(payload)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate with email and password",
)
async def login(
    payload: LoginRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """Authenticate credentials and return JWT tokens (with HttpOnly refresh cookie)."""
    service = AuthService(db)
    token_response, refresh_token = await service.login(payload)
    _set_refresh_cookie(response, refresh_token)
    return token_response


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token using refresh token cookie or payload",
)
async def refresh(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    payload: RefreshRequest | None = None,
) -> TokenResponse:
    """Exchange an active refresh token for a rotated token pair."""
    token_from_cookie = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    token_from_body = payload.refresh_token if payload and payload.refresh_token else None
    token_to_use = token_from_body or token_from_cookie

    if not token_to_use:
        raise AuthenticationError(
            message="Refresh token not found in cookie or request body",
            code="REFRESH_TOKEN_REQUIRED",
        )

    service = AuthService(db)
    token_response, new_refresh_token = await service.refresh(token_to_use)
    _set_refresh_cookie(response, new_refresh_token)
    return token_response


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout and invalidate the active refresh session",
)
async def logout(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    payload: RefreshRequest | None = None,
) -> None:
    """Invalidate the refresh token session and remove the auth cookie."""
    token_from_cookie = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    token_from_body = payload.refresh_token if payload and payload.refresh_token else None
    token_to_use = token_from_body or token_from_cookie

    service = AuthService(db)
    if token_to_use:
        await service.logout(refresh_token=token_to_use)

    _clear_refresh_cookie(response)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current authenticated user profile",
)
async def get_me(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> UserResponse:
    """Return the profile of the currently authenticated user."""
    return UserResponse.model_validate(current_user)
