"""Browser utilities — Miami-Dade Cloudflare session (auth.json)."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.api.v1.auth import get_current_active_user
from app.browser.manual_portal_browser import validate_storage_state_bytes
from app.browser.miami_dade_tax_auth import resolve_miami_dade_tax_auth_path
from app.browser.tax_browser_profile import profile_status_metadata
from app.core.config import settings
from app.models.user import User

router = APIRouter(prefix="/browser-tools", tags=["browser-tools"])


@router.get("/miami-dade-tax-auth/status", summary="Miami-Dade Cloudflare session status")
async def miami_dade_tax_auth_status(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """Whether backend/auth.json exists and how many cookies it contains (no secret values)."""
    _ = current_user
    path = resolve_miami_dade_tax_auth_path()
    enabled = bool(getattr(settings, "MIAMI_DADE_TAX_AUTH_ENABLED", True))
    meta = profile_status_metadata()
    if not path:
        return {
            "enabled": enabled,
            "configured": False,
            "file_name": getattr(settings, "MIAMI_DADE_TAX_AUTH_FILE", "auth.json"),
            "cookie_count": 0,
            "origin_count": 0,
            "message": "No auth file on the worker yet. Run the tax browser helper on your PC or upload auth.json.",
            **meta,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cookies = data.get("cookies") if isinstance(data, dict) else []
        origins = data.get("origins") if isinstance(data, dict) else []
        return {
            "enabled": enabled,
            "configured": True,
            "file_name": path.name,
            "cookie_count": len(cookies) if isinstance(cookies, list) else 0,
            "origin_count": len(origins) if isinstance(origins, list) else 0,
            "message": "Worker will reuse this session for Miami-Dade tax runs.",
            **meta,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"auth file is unreadable: {exc}",
        ) from exc


@router.post("/miami-dade-tax-auth/upload", summary="Upload Playwright auth.json for Miami-Dade tax")
async def upload_miami_dade_tax_auth(
    current_user: Annotated[User, Depends(get_current_active_user)],
    file: UploadFile = File(...),
) -> dict:
    """Save a Playwright storageState JSON exported from the manual browser helper."""
    _ = current_user
    if not file.filename or not file.filename.lower().endswith(".json"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Upload a .json file")

    raw = await file.read()
    if len(raw) > 2_000_000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File too large (max 2MB)")

    try:
        data = validate_storage_state_bytes(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid Playwright storage state: {exc}",
        ) from exc

    rel = getattr(settings, "MIAMI_DADE_TAX_AUTH_FILE", "auth.json")
    from pathlib import Path

    backend_root = Path(__file__).resolve().parent.parent.parent.parent
    dest = Path(rel) if Path(rel).is_absolute() else backend_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)

    return {
        "ok": True,
        "path": dest.name,
        "cookie_count": len(data.get("cookies", [])),
        "message": "Saved. Restart the Celery worker so new tax runs pick up this session.",
    }