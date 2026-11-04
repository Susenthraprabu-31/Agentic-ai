"""User request/response schemas."""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator


class UserCreateRequest(BaseModel):
    """Payload for creating a new user."""

    email: EmailStr
    password: str
    full_name: str | None = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not re.search(r"[A-Za-z]", v):
            raise ValueError("Password must contain at least one letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one number")
        return v

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: EmailStr) -> str:
        return str(v).strip().lower()

    @field_validator("full_name")
    @classmethod
    def clean_full_name(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            return v if v else None
        return None


class UserUpdateRequest(BaseModel):
    """Payload for updating an existing user (all fields optional)."""

    email: EmailStr | None = None
    full_name: str | None = None
    is_active: bool | None = None


class UserResponse(BaseModel):
    """Public representation of a user (no password hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str | None
    is_active: bool
    is_superuser: bool
    created_at: datetime
    updated_at: datetime | None


class TokenResponse(BaseModel):
    """JWT token response returned after authentication."""

    access_token: str
    token_type: str = "bearer"
    refresh_token: str | None = None
    user: UserResponse | None = None


class LoginRequest(BaseModel):
    """Credentials for password-based login."""

    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: EmailStr) -> str:
        return str(v).strip().lower()


class RefreshRequest(BaseModel):
    """Payload for token refresh (optional if refresh token cookie is present)."""

    refresh_token: str | None = None
