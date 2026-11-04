"""Circuit Breakers router — query circuit breaker states and trigger administrative resets."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user, get_current_superuser
from app.db.session import get_db
from app.models.user import User
from app.resilience.circuit_breaker import circuit_breaker

router = APIRouter(prefix="/circuit-breakers", tags=["circuit-breakers"])


@router.get("", summary="List all circuit breakers")
async def list_circuit_breakers(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[dict[str, Any]]:
    """List all external dependency circuit breakers with failure counters, cooldowns, and states."""
    return await circuit_breaker.list_all_circuits()


@router.get("/{dependency_key:path}", summary="Get circuit breaker state for a dependency")
async def get_circuit_breaker(
    dependency_key: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict[str, Any]:
    """Retrieve state, thresholds, and last failure details for a specific dependency circuit."""
    return await circuit_breaker.get_circuit_state(dependency_key)


@router.post("/{dependency_key:path}/reset", summary="Reset circuit breaker to CLOSED")
async def reset_circuit_breaker(
    dependency_key: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict[str, Any]:
    """Manually resets a tripped circuit breaker back to CLOSED state."""
    # Check permissions: any authenticated active user or admin can reset
    return await circuit_breaker.manual_reset(dependency_key)
