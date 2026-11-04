"""Security Events router — read-only access to SecurityEvent records."""

import uuid

from fastapi import APIRouter

router = APIRouter(prefix="/security-events", tags=["security-events"])


@router.get("", summary="List security events")
async def list_security_events(skip: int = 0, limit: int = 50) -> dict:
    return {"items": [], "total": 0, "skip": skip, "limit": limit}


@router.get("/{event_id}", summary="Get security event by ID")
async def get_security_event(event_id: uuid.UUID) -> dict:
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Security event not found")
