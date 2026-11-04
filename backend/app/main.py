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
    )
    yield
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
