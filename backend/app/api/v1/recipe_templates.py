"""UI Automation Templates router — CRUD and version management."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.recipe import (
    CompatibilityCheckResponse,
    TemplateVersionCreateRequest,
    TemplateVersionResponse,
    UITemplateCreateRequest,
    UITemplateListResponse,
    UITemplateResponse,
    UITemplateUpdateRequest,
)
from app.services.recipe_service import RecipeService

router = APIRouter(prefix="/recipe-templates", tags=["recipe-templates"])


@router.get("", response_model=UITemplateListResponse, summary="List UI templates")
async def list_templates(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    pattern_type: str | None = Query(None),
    status: str | None = Query(None),
) -> UITemplateListResponse:
    """Retrieve paginated reusable UI automation templates."""
    service = RecipeService(db)
    return await service.list_templates(
        page=page,
        size=size,
        search=search,
        pattern_type=pattern_type,
        status=status,
        user=current_user,
    )


@router.post(
    "",
    response_model=UITemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create UI template",
)
async def create_template(
    payload: UITemplateCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> UITemplateResponse:
    """Register a new reusable website interaction pattern template."""
    service = RecipeService(db)
    return await service.create_template(payload, current_user)


@router.get("/{template_id}", response_model=UITemplateResponse, summary="Get template by ID")
async def get_template(
    template_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> UITemplateResponse:
    """Get UI template details including version history."""
    service = RecipeService(db)
    return await service.get_template(template_id, current_user)


@router.put("/{template_id}", response_model=UITemplateResponse, summary="Update template")
async def update_template(
    template_id: uuid.UUID,
    payload: UITemplateUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> UITemplateResponse:
    """Update UI template metadata."""
    service = RecipeService(db)
    return await service.update_template(template_id, payload, current_user)


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete template",
)
async def delete_template(
    template_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> None:
    """Delete a UI template."""
    service = RecipeService(db)
    await service.delete_template(template_id, current_user)


# ---------------------------------------------------------------------------
# Template Versions
# ---------------------------------------------------------------------------


@router.get(
    "/{template_id}/versions",
    response_model=list[TemplateVersionResponse],
    summary="List template versions",
)
async def list_template_versions(
    template_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[TemplateVersionResponse]:
    """List immutable versions for a UI template."""
    service = RecipeService(db)
    return await service.list_template_versions(template_id, current_user)


@router.post(
    "/{template_id}/versions",
    response_model=TemplateVersionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create template version",
)
async def create_template_version(
    template_id: uuid.UUID,
    payload: TemplateVersionCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> TemplateVersionResponse:
    """Publish a new immutable version of a UI template definition."""
    service = RecipeService(db)
    return await service.create_template_version(template_id, payload, current_user)


@router.get(
    "/{template_id}/versions/{version_id}",
    response_model=TemplateVersionResponse,
    summary="Get template version",
)
async def get_template_version(
    template_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> TemplateVersionResponse:
    """Get specific template version details."""
    service = RecipeService(db)
    return await service.get_template_version(template_id, version_id, current_user)

