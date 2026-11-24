"""Desktop settings and secure credential management API."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_superuser
from app.models.user import User
from app.services.credential_store import SUPPORTED_KEYS, delete_secret, get_secret, set_secret
from app.storage.storage_service import sync_status

router = APIRouter(prefix="/settings/desktop", tags=["settings"])


class SecretSetRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    value: str = Field(..., min_length=0, max_length=4096)


@router.get("/sync-status")
async def get_sync_status(
    _: Annotated[User, Depends(get_current_superuser)],
) -> dict[str, Any]:
    return await sync_status()


@router.get("/secrets/{name}/configured")
async def secret_configured(
    name: str,
    _: Annotated[User, Depends(get_current_superuser)],
) -> dict[str, bool]:
    if name not in SUPPORTED_KEYS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported secret name")
    return {"configured": bool(get_secret(name))}


@router.post("/secrets")
async def save_secret(
    payload: SecretSetRequest,
    _: Annotated[User, Depends(get_current_superuser)],
) -> dict[str, bool]:
    if payload.name not in SUPPORTED_KEYS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported secret name")
    ok = set_secret(payload.name, payload.value)
    if not ok:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not store secret")
    from app.services.credential_store import apply_stored_secrets_to_settings

    apply_stored_secrets_to_settings()
    return {"ok": True}


@router.delete("/secrets/{name}")
async def remove_secret(
    name: str,
    _: Annotated[User, Depends(get_current_superuser)],
) -> dict[str, bool]:
    if name not in SUPPORTED_KEYS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported secret name")
    return {"ok": delete_secret(name)}