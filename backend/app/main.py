"""FastAPI application factory."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app.api.router import api_router
from app.core.config import settings
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    TitleBaseException,
    ValidationError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info(
        "app.startup",
        name=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        desktop=settings.DESKTOP_MODE,
    )
    if settings.DESKTOP_MODE:
        from app.db.desktop_bootstrap import bootstrap_desktop_database
        from app.services.credential_store import apply_stored_secrets_to_settings
        from app.storage.storage_service import start_storage_sync_worker

        apply_stored_secrets_to_settings()
        await bootstrap_desktop_database()
        await start_storage_sync_worker()
    if settings.JOB_RUNNER_ENABLED:
        from app.workers.job_runner import start_local_job_runner
        from app.workers.scheduler import start_local_scheduler

        await start_local_job_runner()
        await start_local_scheduler()
    yield
    if settings.JOB_RUNNER_ENABLED:
        from app.workers.job_runner import stop_local_job_runner
        from app.workers.scheduler import stop_local_scheduler

        await stop_local_job_runner()
        await stop_local_scheduler()
    if settings.DESKTOP_MODE:
        from app.storage.storage_service import stop_storage_sync_worker

        await stop_storage_sync_worker()
    logger.info("app.shutdown", name=settings.APP_NAME)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "Title Search Platform API — automated county assessor and tax record retrieval."
        ),
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if settings.DESKTOP_MODE:

        @application.middleware("http")
        async def localhost_guard(request: Request, call_next):  # type: ignore[no-untyped-def]
            host = (request.client.host if request.client else "") or ""
            if host not in ("127.0.0.1", "::1", "localhost"):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Desktop API is only available on localhost", "code": "FORBIDDEN"},
                )
            return await call_next(request)

    # ------------------------------------------------------------------
    # Routers
    # ------------------------------------------------------------------
    application.include_router(api_router)

    # ------------------------------------------------------------------
    # Exception handlers
    # ------------------------------------------------------------------

    @application.exception_handler(TitleBaseException)
    async def title_exception_handler(request: Request, exc: TitleBaseException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": exc.message, "code": exc.code},
        )

    @application.exception_handler(NotImplementedError)
    async def not_implemented_handler(request: Request, exc: NotImplementedError) -> JSONResponse:
        import traceback
        tb = traceback.format_exc()
        logger.error("NotImplementedError on %s: %s\n%s", request.url, exc, tb)
        return JSONResponse(
            status_code=501,
            content={"detail": str(exc) or "This endpoint is not yet implemented.", "traceback": tb, "code": "NOT_IMPLEMENTED"},
        )

    # ------------------------------------------------------------------
    # Root redirect
    # ------------------------------------------------------------------

    @application.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url=f"{settings.API_V1_PREFIX}/health")

    return application


app: FastAPI = create_app()
