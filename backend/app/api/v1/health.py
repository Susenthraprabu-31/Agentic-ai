"""Health check router — liveness, readiness, and full health."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.schemas.health import HealthResponse, LivenessResponse

router = APIRouter(prefix="/health", tags=["health"])
logger = get_logger(__name__)


@router.api_route("/live", methods=["GET", "HEAD"], response_model=LivenessResponse, summary="Liveness probe")
async def liveness() -> LivenessResponse:
    """Liveness probe — GET/HEAD return 200 if the process is alive (wait-on uses HEAD)."""
    return LivenessResponse(status="ok")


@router.get("/ready", response_model=HealthResponse, summary="Readiness probe")
async def readiness(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    """Readiness probe — checks DB and Redis connectivity."""
    return await _build_health_response(db)


@router.get("", response_model=HealthResponse, summary="Full health check")
async def health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    """Full health check including all external dependencies."""
    return await _build_health_response(db)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _check_db(db: AsyncSession) -> str:
    try:
        await db.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:
        logger.warning("health.db_check_failed", error=str(exc))
        return f"error: {exc}"


async def _check_redis() -> str:
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        await client.ping()
        await client.aclose()
        return "ok"
    except Exception as exc:
        logger.warning("health.redis_check_failed", error=str(exc))
        return f"error: {exc}"


async def _build_health_response(db: AsyncSession) -> HealthResponse:
    db_status = await _check_db(db)
    if settings.USE_REDIS:
        redis_status = await _check_redis()
    else:
        redis_status = "skipped"
    redis_ok = redis_status in ("ok", "skipped")
    overall = "ok" if db_status == "ok" and redis_ok else "degraded"
    return HealthResponse(
        status=overall,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        database=db_status,
        redis=redis_status,
        timestamp=datetime.now(timezone.utc),
    )
