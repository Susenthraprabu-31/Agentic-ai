"""Recipes router — CRUD for Recipes, Versions, Publication, and Validation Cases."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.recipe import (
    RecipeCreateRequest,
    RecipeListResponse,
    RecipeResponse,
    RecipeUpdateRequest,
    RecipeVersionCreateRequest,
    RecipeVersionResponse,
    ValidationCaseCreateRequest,
    ValidationCaseResponse,
    ValidationCaseUpdateRequest,
)
from app.services.recipe_service import RecipeService

router = APIRouter(prefix="/recipes", tags=["recipes"])


class RecipeVersionUpdateRequest(BaseModel):
    definition: dict[str, Any]
    notes: str | None = None


class AnalyzePageRequest(BaseModel):
    county_source_id: uuid.UUID
    record_type: str = "assessor"
    portal_url: str | None = None


# ---------------------------------------------------------------------------
# AI Page Analysis & Recipe Generation
# ---------------------------------------------------------------------------


@router.post("/analyze-page", summary="Analyze county search page with AI and save recipe actions")
async def analyze_page(
    payload: AnalyzePageRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict[str, Any]:
    """Inspects a county portal, analyzes tabs/inputs using AI/DOM heuristics, and saves a multi-tab recipe to the database."""
    from app.services.ai_page_analyzer import AIPageAnalyzerService
    analyzer = AIPageAnalyzerService(db)
    return await analyzer.analyze_and_save_recipe(
        county_source_id=payload.county_source_id,
        record_type=payload.record_type,
        portal_url=payload.portal_url,
        user_id=current_user.id,
    )


# ---------------------------------------------------------------------------
# Recipes CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=RecipeListResponse, summary="List recipes")
async def list_recipes(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    county_source_id: uuid.UUID | None = Query(None),
    record_type: str | None = Query(None),
    status: str | None = Query(None),
) -> RecipeListResponse:
    """List recipes with pagination and filtering."""
    service = RecipeService(db)
    return await service.list_recipes(
        page=page,
        size=size,
        search=search,
        county_source_id=county_source_id,
        record_type=record_type,
        status=status,
        user=current_user,
    )


@router.post(
    "",
    response_model=RecipeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create recipe",
)
async def create_recipe(
    payload: RecipeCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> RecipeResponse:
    """Create a new recipe tied to a county source and UI template."""
    service = RecipeService(db)
    return await service.create_recipe(payload, current_user)


@router.get("/{recipe_id}", response_model=RecipeResponse, summary="Get recipe by ID")
async def get_recipe(
    recipe_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> RecipeResponse:
    """Get recipe details including version list."""
    service = RecipeService(db)
    return await service.get_recipe(recipe_id, current_user)


@router.put("/{recipe_id}", response_model=RecipeResponse, summary="Update recipe")
async def update_recipe(
    recipe_id: uuid.UUID,
    payload: RecipeUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> RecipeResponse:
    """Update recipe metadata."""
    service = RecipeService(db)
    return await service.update_recipe(recipe_id, payload, current_user)


@router.delete(
    "/{recipe_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete recipe",
)
async def delete_recipe(
    recipe_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> None:
    """Delete a recipe and its version history."""
    service = RecipeService(db)
    await service.delete_recipe(recipe_id, current_user)


# ---------------------------------------------------------------------------
# Recipe Versions & Lifecycle Management
# ---------------------------------------------------------------------------


@router.get(
    "/{recipe_id}/versions",
    response_model=list[RecipeVersionResponse],
    summary="List recipe versions",
)
async def list_recipe_versions(
    recipe_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[RecipeVersionResponse]:
    """List all version records for a recipe."""
    service = RecipeService(db)
    return await service.list_recipe_versions(recipe_id, current_user)


@router.post(
    "/{recipe_id}/versions",
    response_model=RecipeVersionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create new recipe version",
)
async def create_recipe_version(
    recipe_id: uuid.UUID,
    payload: RecipeVersionCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> RecipeVersionResponse:
    """Create a new version for this recipe."""
    service = RecipeService(db)
    return await service.create_recipe_version(recipe_id, payload, current_user)


@router.get(
    "/{recipe_id}/versions/{version_id}",
    response_model=RecipeVersionResponse,
    summary="Get recipe version",
)
async def get_recipe_version(
    recipe_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> RecipeVersionResponse:
    """Get specific version definition."""
    service = RecipeService(db)
    return await service.get_recipe_version(recipe_id, version_id, current_user)


@router.put(
    "/{recipe_id}/versions/{version_id}",
    response_model=RecipeVersionResponse,
    summary="Update recipe version definition",
)
async def update_recipe_version(
    recipe_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: RecipeVersionUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> RecipeVersionResponse:
    """Update definition of an un-published recipe version (enforces immutability)."""
    service = RecipeService(db)
    return await service.update_recipe_version_definition(
        recipe_id, version_id, payload.definition, payload.notes, current_user
    )


@router.post(
    "/{recipe_id}/versions/{version_id}/publish",
    response_model=RecipeVersionResponse,
    summary="Publish recipe version",
)
async def publish_recipe_version(
    recipe_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> RecipeVersionResponse:
    """Transition version from approved/validated to Published (freezes immutability)."""
    service = RecipeService(db)
    return await service.publish_recipe_version(recipe_id, version_id, current_user)


@router.post(
    "/{recipe_id}/versions/{version_id}/deprecate",
    response_model=RecipeVersionResponse,
    summary="Deprecate recipe version",
)
async def deprecate_recipe_version(
    recipe_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> RecipeVersionResponse:
    """Mark recipe version as deprecated without deleting historical records."""
    service = RecipeService(db)
    return await service.deprecate_recipe_version(recipe_id, version_id, current_user)


# ---------------------------------------------------------------------------
# Validation Cases
# ---------------------------------------------------------------------------


@router.get(
    "/{recipe_id}/validation-cases",
    response_model=list[ValidationCaseResponse],
    summary="List validation test cases",
)
async def list_validation_cases(
    recipe_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[ValidationCaseResponse]:
    """Retrieve validation cases for testing recipe correctness."""
    service = RecipeService(db)
    return await service.list_validation_cases(recipe_id, current_user)


@router.post(
    "/{recipe_id}/validation-cases",
    response_model=ValidationCaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create validation test case",
)
async def create_validation_case(
    recipe_id: uuid.UUID,
    payload: ValidationCaseCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> ValidationCaseResponse:
    """Register a new validation test case with expected output values."""
    service = RecipeService(db)
    return await service.create_validation_case(recipe_id, payload, current_user)


@router.get(
    "/{recipe_id}/validation-cases/{case_id}",
    response_model=ValidationCaseResponse,
    summary="Get validation test case",
)
async def get_validation_case(
    recipe_id: uuid.UUID,
    case_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> ValidationCaseResponse:
    """Get validation test case details."""
    service = RecipeService(db)
    return await service.get_validation_case(recipe_id, case_id, current_user)


@router.put(
    "/{recipe_id}/validation-cases/{case_id}",
    response_model=ValidationCaseResponse,
    summary="Update validation test case",
)
async def update_validation_case(
    recipe_id: uuid.UUID,
    case_id: uuid.UUID,
    payload: ValidationCaseUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> ValidationCaseResponse:
    """Update validation test case parameters or record test pass status."""
    service = RecipeService(db)
    return await service.update_validation_case(recipe_id, case_id, payload, current_user)


@router.delete(
    "/{recipe_id}/validation-cases/{case_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete validation test case",
)
async def delete_validation_case(
    recipe_id: uuid.UUID,
    case_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> None:
    """Delete a validation test case."""
    service = RecipeService(db)
    await service.delete_validation_case(recipe_id, case_id, current_user)
