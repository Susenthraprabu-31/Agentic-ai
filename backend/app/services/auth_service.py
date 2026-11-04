"""Auth service — user registration, login, token refresh, revocation, and logout."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError, ConflictError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)
from app.models.user import RefreshToken, User
from app.schemas.user import LoginRequest, TokenResponse, UserCreateRequest, UserResponse

logger = get_logger(__name__)


class AuthService:
    """Encapsulates authentication and user account lifecycle logic."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def register(self, payload: UserCreateRequest) -> UserResponse:
        """Create a new user account with validated credentials.

        Raises:
            ConflictError: If a user with the normalized email already exists.
        """
        normalized_email = payload.email.strip().lower()

        # Check for existing email (case-insensitive)
        result = await self._db.execute(
            select(User).where(func.lower(User.email) == normalized_email)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            logger.warning("auth.register_conflict", email=normalized_email)
            raise ConflictError(
                message="An account with this email address already exists",
                code="EMAIL_ALREADY_EXISTS",
            )

        hashed = hash_password(payload.password)
        new_user = User(
            email=normalized_email,
            hashed_password=hashed,
            full_name=payload.full_name,
            is_active=True,
            is_superuser=False,
        )

        self._db.add(new_user)
        await self._db.commit()
        await self._db.refresh(new_user)

        logger.info("auth.registered_successfully", user_id=str(new_user.id), email=normalized_email)
        return UserResponse.model_validate(new_user)

    async def login(self, payload: LoginRequest) -> tuple[TokenResponse, str]:
        """Authenticate user with credentials and issue a fresh token pair.

        Returns:
            Tuple of (TokenResponse, raw_refresh_token_string)
        """
        normalized_email = payload.email.strip().lower()

        result = await self._db.execute(
            select(User).where(func.lower(User.email) == normalized_email)
        )
        user = result.scalar_one_or_none()

        if user is None or not verify_password(payload.password, user.hashed_password):
            logger.warning("auth.login_failed_invalid_credentials", email=normalized_email)
            raise AuthenticationError(
                message="Invalid email or password",
                code="INVALID_CREDENTIALS",
            )

        if not user.is_active:
            logger.warning("auth.login_failed_inactive_user", user_id=str(user.id))
            raise AuthenticationError(
                message="User account is deactivated",
                code="USER_INACTIVE",
            )

        user_id_str = str(user.id)
        access_token = create_access_token(subject=user_id_str)
        refresh_token, jti, expires_at = create_refresh_token(subject=user_id_str)

        # Persist refresh token session for rotation/revocation tracking
        token_record = RefreshToken(
            user_id=user.id,
            jti=jti,
            revoked=False,
            expires_at=expires_at,
        )
        self._db.add(token_record)
        await self._db.commit()

        token_response = TokenResponse(
            access_token=access_token,
            token_type="bearer",
            refresh_token=refresh_token,
            user=UserResponse.model_validate(user),
        )

        logger.info("auth.login_success", user_id=user_id_str)
        return token_response, refresh_token

    async def refresh(self, refresh_token: str) -> tuple[TokenResponse, str]:
        """Rotate an active refresh token: validate, revoke old, issue new pair.

        Returns:
            Tuple of (new_TokenResponse, new_raw_refresh_token_string)
        """
        payload = decode_refresh_token(refresh_token)
        jti = payload["jti"]
        user_id_str = payload["sub"]

        try:
            user_id = uuid.UUID(user_id_str)
        except (ValueError, TypeError) as exc:
            raise AuthenticationError(
                message="Invalid user identifier in refresh token",
                code="INVALID_TOKEN_SUBJECT",
            ) from exc

        # Find the tracked refresh token in database
        result = await self._db.execute(
            select(RefreshToken).where(RefreshToken.jti == jti)
        )
        token_record = result.scalar_one_or_none()

        if token_record is None:
            logger.warning("auth.refresh_token_not_found", jti=jti)
            raise AuthenticationError(
                message="Refresh token is unrecognized or invalid",
                code="INVALID_REFRESH_TOKEN",
            )

        if token_record.revoked:
            logger.warning("auth.refresh_token_already_revoked", jti=jti, user_id=user_id_str)
            raise AuthenticationError(
                message="Refresh token has been revoked",
                code="TOKEN_REVOKED",
            )

        now = datetime.now(timezone.utc)
        if token_record.expires_at.tzinfo is None:
            token_expires_at = token_record.expires_at.replace(tzinfo=timezone.utc)
        else:
            token_expires_at = token_record.expires_at

        if token_expires_at <= now:
            logger.warning("auth.refresh_token_expired", jti=jti)
            raise AuthenticationError(
                message="Refresh token has expired",
                code="TOKEN_EXPIRED",
            )

        # Check user status
        user_result = await self._db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()

        if user is None or not user.is_active:
            raise AuthenticationError(
                message="User account no longer exists or is inactive",
                code="USER_INACTIVE",
            )

        # Revoke the used refresh token (Token Rotation)
        token_record.revoked = True

        # Generate a new token pair
        new_access_token = create_access_token(subject=user_id_str)
        new_refresh_token, new_jti, new_expires_at = create_refresh_token(subject=user_id_str)

        new_record = RefreshToken(
            user_id=user.id,
            jti=new_jti,
            revoked=False,
            expires_at=new_expires_at,
        )
        self._db.add(new_record)
        await self._db.commit()

        token_response = TokenResponse(
            access_token=new_access_token,
            token_type="bearer",
            refresh_token=new_refresh_token,
            user=UserResponse.model_validate(user),
        )

        logger.info("auth.token_rotated_successfully", user_id=user_id_str, old_jti=jti, new_jti=new_jti)
        return token_response, new_refresh_token

    async def logout(self, refresh_token: str | None = None, user_id: uuid.UUID | None = None) -> None:
        """Invalidate the specified refresh token session or all sessions for user."""
        if refresh_token:
            try:
                payload = decode_refresh_token(refresh_token)
                jti = payload.get("jti")
                if jti:
                    result = await self._db.execute(
                        select(RefreshToken).where(RefreshToken.jti == jti)
                    )
                    token_record = result.scalar_one_or_none()
                    if token_record:
                        token_record.revoked = True
                        await self._db.commit()
                        logger.info("auth.session_logged_out", jti=jti)
                        return
            except Exception as exc:
                logger.debug("auth.logout_token_decode_error", error=str(exc))

        if user_id:
            # Revoke all active tokens for this user
            result = await self._db.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == user_id,
                    RefreshToken.revoked == False,  # noqa: E712
                )
            )
            active_tokens = result.scalars().all()
            for t in active_tokens:
                t.revoked = True
            await self._db.commit()
            logger.info("auth.all_sessions_revoked_for_user", user_id=str(user_id))
