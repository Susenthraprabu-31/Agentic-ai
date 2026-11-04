"""County Sources router — CRUD for CountySource and SourceConfiguration resources."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.county_source import (
    CountySourceCreateRequest,
    CountySourceListResponse,
    CountySourceResponse,
    CountySourceUpdateRequest,
    SourceConfigCreateRequest,
    SourceConfigResponse,
    SourceConfigUpdateRequest,
)
from app.services.county_service import CountyService

router = APIRouter(prefix="/county-sources", tags=["county-sources"])


# ---------------------------------------------------------------------------
# County Sources CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=CountySourceListResponse, summary="List county sources")
async def list_county_sources(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    size: int = Query(20, ge=1, le=100, description="Items per page"),
    search: str | None = Query(None, description="Search term across name, county, and state"),
    state: str | None = Query(None, description="Filter by 2-letter state code (e.g. FL, CA)"),
    status: str | None = Query(None, description="Filter by status (active, inactive, pending)"),
    is_active: bool | None = Query(None, description="Filter by active flag"),
    sort_by: str = Query("created_at", description="Sort column (e.g. name, county, state, created_at)"),
    sort_dir: str = Query("desc", description="Sort direction ('asc' or 'desc')"),
) -> CountySourceListResponse:
    """Retrieve a paginated, filterable list of county sources."""
    service = CountyService(db)
    return await service.list_sources(
        page=page,
        size=size,
        search=search,
        state=state,
        status=status,
        is_active=is_active,
        sort_by=sort_by,
        sort_dir=sort_dir,
        user=current_user,
    )


@router.post(
    "",
    response_model=CountySourceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create county source",
)
async def create_county_source(
    payload: CountySourceCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> CountySourceResponse:
    """Register a new county data source."""
    service = CountyService(db)
    return await service.create_source(payload, current_user)


@router.get("/{source_id}", response_model=CountySourceResponse, summary="Get county source by ID")
async def get_county_source(
    source_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> CountySourceResponse:
    """Retrieve details of a specific county source including its configurations."""
    service = CountyService(db)
    return await service.get_source(source_id, current_user)


@router.put("/{source_id}", response_model=CountySourceResponse, summary="Update county source")
async def update_county_source(
    source_id: uuid.UUID,
    payload: CountySourceUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> CountySourceResponse:
    """Update details of a county source."""
    service = CountyService(db)
    return await service.update_source(source_id, payload, current_user)


@router.delete(
    "/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete county source",
)
async def delete_county_source(
    source_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> None:
    """Delete a county source and its attached configurations."""
    service = CountyService(db)
    await service.delete_source(source_id, current_user)


# ---------------------------------------------------------------------------
# Source Configurations (Assessor / Tax Website Selectors)
# ---------------------------------------------------------------------------


@router.post(
    "/{source_id}/configurations",
    response_model=SourceConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an Assessor or Tax configuration to a county source",
)
async def create_source_configuration(
    source_id: uuid.UUID,
    payload: SourceConfigCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SourceConfigResponse:
    """Register website-specific selectors and settings for Assessor or Tax searches."""
    service = CountyService(db)
    return await service.create_configuration(source_id, payload, current_user)


@router.get(
    "/{source_id}/configurations",
    response_model=list[SourceConfigResponse],
    summary="List configurations for a county source",
)
async def list_source_configurations(
    source_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[SourceConfigResponse]:
    """Retrieve all website configurations for this county source."""
    service = CountyService(db)
    return await service.list_configurations(source_id, current_user)


@router.get(
    "/{source_id}/configurations/{config_id}",
    response_model=SourceConfigResponse,
    summary="Get configuration details",
)
async def get_source_configuration(
    source_id: uuid.UUID,
    config_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SourceConfigResponse:
    """Retrieve details of a specific configuration."""
    service = CountyService(db)
    return await service.get_configuration(source_id, config_id, current_user)


@router.put(
    "/{source_id}/configurations/{config_id}",
    response_model=SourceConfigResponse,
    summary="Update configuration",
)
async def update_source_configuration(
    source_id: uuid.UUID,
    config_id: uuid.UUID,
    payload: SourceConfigUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SourceConfigResponse:
    """Update website-specific selectors or parameters."""
    service = CountyService(db)
    return await service.update_configuration(source_id, config_id, payload, current_user)


@router.delete(
    "/{source_id}/configurations/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete configuration",
)
async def delete_source_configuration(
    source_id: uuid.UUID,
    config_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> None:
    """Delete a configuration."""
    service = CountyService(db)
    await service.delete_configuration(source_id, config_id, current_user)


@router.post(
    "/{source_id}/analyze-recipe",
    status_code=status.HTTP_200_OK,
    summary="Analyze county website with AI and save recipe actions to DB",
)
async def analyze_county_recipe(
    source_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
    record_type: str = "assessor",
    portal_url: str | None = None,
    payload: Annotated[dict[str, Any], Body()] = {},
) -> dict[str, Any]:
    """Inspects a county portal, analyzes tabs/inputs using AI/DOM heuristics, and saves a multi-tab recipe to the database."""
    effective_record_type = (payload or {}).get("record_type") or record_type or "assessor"
    effective_portal_url = (payload or {}).get("portal_url") or portal_url
    from app.services.ai_page_analyzer import AIPageAnalyzerService
    analyzer = AIPageAnalyzerService(db)
    return await analyzer.analyze_and_save_recipe(
        county_source_id=source_id,
        record_type=effective_record_type,
        portal_url=effective_portal_url,
        user_id=current_user.id,
    )
