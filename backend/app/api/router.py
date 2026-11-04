"""Central API router — mounts all v1 sub-routers."""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.circuit_breakers import router as circuit_breakers_router
from app.api.v1.county_sources import router as county_sources_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.documents import router as documents_router
from app.api.v1.executions import router as executions_router
from app.api.v1.health import router as health_router
from app.api.v1.recipe_templates import router as recipe_templates_router
from app.api.v1.recipes import router as recipes_router
from app.api.v1.results import router as results_router
from app.api.v1.security_events import router as security_events_router
from app.api.v1.workers import router as workers_router
from app.api.v1.workflows import router as workflows_router
from app.core.config import settings

api_router = APIRouter(prefix=settings.API_V1_PREFIX)

api_router.include_router(health_router)
api_router.include_router(dashboard_router)
api_router.include_router(auth_router)
api_router.include_router(workflows_router)
api_router.include_router(county_sources_router)
api_router.include_router(recipe_templates_router)
api_router.include_router(recipes_router)
api_router.include_router(executions_router)
api_router.include_router(documents_router)
api_router.include_router(results_router)
api_router.include_router(security_events_router)
api_router.include_router(workers_router)
api_router.include_router(circuit_breakers_router)
