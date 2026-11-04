"""Recipe and UI Template service — business logic, lifecycle states, and immutability."""

import math
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.models.county_source import CountySource, SourceConfiguration, TemplateVersion, UITemplate
from app.models.recipe import (
    Recipe,
    RecipeLifecycleStatus,
    RecipeVersion,
    ValidationCase,
)
from app.models.user import User
from app.schemas.recipe import (
    RecipeCreateRequest,
    RecipeListResponse,
    RecipeResponse,
    RecipeUpdateRequest,
    RecipeVersionCreateRequest,
    RecipeVersionResponse,
    TemplateVersionCreateRequest,
    TemplateVersionResponse,
    UITemplateCreateRequest,
    UITemplateListResponse,
    UITemplateResponse,
    UITemplateUpdateRequest,
    ValidationCaseCreateRequest,
    ValidationCaseResponse,
    ValidationCaseUpdateRequest,
)
from app.services.recipe_validator import (
    check_template_compatibility,
    validate_recipe_definition,
)

logger = get_logger(__name__)


class RecipeService:
    """Service handling UI templates, recipes, version immutability, and validation test cases."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # -------------------------------------------------------------------------
    # UI Templates
    # -------------------------------------------------------------------------

    async def list_templates(
        self,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        pattern_type: str | None = None,
        status: str | None = None,
        user: User | None = None,
    ) -> UITemplateListResponse:
        """List paginated reusable UI automation templates."""
        query = select(UITemplate).options(selectinload(UITemplate.versions))

        if search:
            pattern = f"%{search.strip().lower()}%"
            query = query.where(
                or_(
                    func.lower(UITemplate.name).like(pattern),
                    func.lower(UITemplate.description).like(pattern),
                )
            )

        if pattern_type and pattern_type.upper() != "ALL":
            query = query.where(UITemplate.pattern_type == pattern_type.strip().upper())

        if status and status.lower() != "all":
            query = query.where(UITemplate.status == status.strip().lower())

        count_query = select(func.count()).select_from(query.subquery())
        total = (await self._db.execute(count_query)).scalar() or 0

        offset = (max(page, 1) - 1) * max(size, 1)
        query = query.order_by(UITemplate.created_at.desc()).offset(offset).limit(size)

        result = await self._db.execute(query)
        templates = result.scalars().all()
        total_pages = math.ceil(total / size) if size > 0 else 1

        items = [UITemplateResponse.model_validate(t) for t in templates]
        return UITemplateListResponse(
            items=items,
            total=total,
            page=page,
            size=size,
            pages=total_pages,
        )

    async def create_template(
        self, payload: UITemplateCreateRequest, user: User
    ) -> UITemplateResponse:
        """Create a new reusable UI automation template."""
        new_template = UITemplate(
            name=payload.name.strip(),
            description=payload.description.strip() if payload.description else None,
            pattern_type=payload.pattern_type,
            supported_source_types=payload.supported_source_types,
            supported_search_methods=payload.supported_search_methods,
            required_configuration_fields=payload.required_configuration_fields,
            default_config_schema=payload.default_config_schema,
            tags=payload.tags,
            status="active",
            created_by=user.id,
        )
        self._db.add(new_template)
        await self._db.flush()

        # Seed initial version 1 if definition provided
        initial_def = payload.initial_definition or {}
        v1 = TemplateVersion(
            template_id=new_template.id,
            version_number=1,
            definition=initial_def,
            changelog="Initial template release",
            is_current=True,
            created_by=user.id,
        )
        self._db.add(v1)

        await self._db.commit()
        return await self.get_template(new_template.id, user)

    async def get_template(self, template_id: uuid.UUID, user: User) -> UITemplateResponse:
        """Get template by ID with all versions."""
        query = (
            select(UITemplate)
            .where(UITemplate.id == template_id)
            .options(selectinload(UITemplate.versions))
        )
        result = await self._db.execute(query)
        template = result.scalar_one_or_none()
        if template is None:
            raise NotFoundError(
                message=f"UI Template {template_id} not found",
                code="TEMPLATE_NOT_FOUND",
            )
        return UITemplateResponse.model_validate(template)

    async def update_template(
        self, template_id: uuid.UUID, payload: UITemplateUpdateRequest, user: User
    ) -> UITemplateResponse:
        """Update template metadata."""
        query = select(UITemplate).where(UITemplate.id == template_id)
        template = (await self._db.execute(query)).scalar_one_or_none()
        if template is None:
            raise NotFoundError(
                message=f"UI Template {template_id} not found",
                code="TEMPLATE_NOT_FOUND",
            )

        if template.created_by and template.created_by != user.id and not user.is_superuser:
            raise AuthorizationError(
                message="You do not have permission to modify this template",
                code="FORBIDDEN_TEMPLATE",
            )

        update_data = payload.model_dump(exclude_unset=True)
        for k, v in update_data.items():
            setattr(template, k, v)

        await self._db.commit()
        return await self.get_template(template_id, user)

    async def delete_template(self, template_id: uuid.UUID, user: User) -> None:
        """Delete UI template."""
        query = select(UITemplate).where(UITemplate.id == template_id)
        template = (await self._db.execute(query)).scalar_one_or_none()
        if template is None:
            raise NotFoundError(
                message=f"UI Template {template_id} not found",
                code="TEMPLATE_NOT_FOUND",
            )

        if template.created_by and template.created_by != user.id and not user.is_superuser:
            raise AuthorizationError(
                message="You do not have permission to delete this template",
                code="FORBIDDEN_TEMPLATE",
            )

        await self._db.delete(template)
        await self._db.commit()

    # -------------------------------------------------------------------------
    # Template Versions
    # -------------------------------------------------------------------------

    async def list_template_versions(
        self, template_id: uuid.UUID, user: User
    ) -> list[TemplateVersionResponse]:
        """List all versions of a template."""
        await self.get_template(template_id, user)
        result = await self._db.execute(
            select(TemplateVersion)
            .where(TemplateVersion.template_id == template_id)
            .order_by(TemplateVersion.version_number.desc())
        )
        versions = result.scalars().all()
        return [TemplateVersionResponse.model_validate(v) for v in versions]

    async def create_template_version(
        self,
        template_id: uuid.UUID,
        payload: TemplateVersionCreateRequest,
        user: User,
    ) -> TemplateVersionResponse:
        """Create a new immutable template version."""
        template_res = await self._db.execute(
            select(UITemplate)
            .where(UITemplate.id == template_id)
            .options(selectinload(UITemplate.versions))
        )
        template = template_res.scalar_one_or_none()
        if template is None:
            raise NotFoundError(f"Template {template_id} not found", code="TEMPLATE_NOT_FOUND")

        # Determine next version number
        highest_v = 0
        for v in template.versions:
            if v.version_number > highest_v:
                highest_v = v.version_number
            if payload.is_current:
                v.is_current = False

        next_version = highest_v + 1

        new_version = TemplateVersion(
            template_id=template_id,
            version_number=next_version,
            definition=payload.definition,
            changelog=payload.changelog,
            is_current=payload.is_current or (highest_v == 0),
            created_by=user.id,
        )
        self._db.add(new_version)
        await self._db.commit()
        await self._db.refresh(new_version)
        return TemplateVersionResponse.model_validate(new_version)

    async def get_template_version(
        self, template_id: uuid.UUID, version_id: uuid.UUID, user: User
    ) -> TemplateVersionResponse:
        """Get a specific template version."""
        result = await self._db.execute(
            select(TemplateVersion).where(
                TemplateVersion.id == version_id,
                TemplateVersion.template_id == template_id,
            )
        )
        v = result.scalar_one_or_none()
        if v is None:
            raise NotFoundError("Template version not found", code="VERSION_NOT_FOUND")
        return TemplateVersionResponse.model_validate(v)

    # -------------------------------------------------------------------------
    # Recipes
    # -------------------------------------------------------------------------

    async def list_recipes(
        self,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        county_source_id: uuid.UUID | None = None,
        record_type: str | None = None,
        status: str | None = None,
        user: User | None = None,
    ) -> RecipeListResponse:
        """List recipes with search and filtering."""
        query = select(Recipe).options(selectinload(Recipe.versions))

        if search:
            pattern = f"%{search.strip().lower()}%"
            query = query.where(func.lower(Recipe.name).like(pattern))

        if county_source_id:
            query = query.where(Recipe.county_source_id == county_source_id)

        if record_type and record_type.lower() != "all":
            query = query.where(Recipe.record_type == record_type.strip().lower())

        if status and status.lower() != "all":
            query = query.where(Recipe.status == status.strip().lower())

        count_query = select(func.count()).select_from(query.subquery())
        total = (await self._db.execute(count_query)).scalar() or 0

        offset = (max(page, 1) - 1) * max(size, 1)
        query = query.order_by(Recipe.created_at.desc()).offset(offset).limit(size)

        result = await self._db.execute(query)
        recipes = result.scalars().all()
        total_pages = math.ceil(total / size) if size > 0 else 1

        items = [RecipeResponse.model_validate(r) for r in recipes]
        return RecipeListResponse(
            items=items,
            total=total,
            page=page,
            size=size,
            pages=total_pages,
        )

    async def create_recipe(self, payload: RecipeCreateRequest, user: User) -> RecipeResponse:
        """Create a new recipe and initialize Version 1."""
        # 1. Verify county source exists
        source_res = await self._db.execute(
            select(CountySource).where(CountySource.id == payload.county_source_id)
        )
        if source_res.scalar_one_or_none() is None:
            raise NotFoundError(
                message=f"County source {payload.county_source_id} not found",
                code="COUNTY_SOURCE_NOT_FOUND",
            )

        # 2. Check template compatibility if template + source config are both provided
        if payload.ui_template_id and payload.source_configuration_id:
            tpl_res = await self._db.execute(
                select(UITemplate).where(UITemplate.id == payload.ui_template_id)
            )
            tpl = tpl_res.scalar_one_or_none()
            cfg_res = await self._db.execute(
                select(SourceConfiguration).where(
                    SourceConfiguration.id == payload.source_configuration_id
                )
            )
            cfg = cfg_res.scalar_one_or_none()

            if tpl and cfg:
                is_compat, errors = check_template_compatibility(tpl, cfg)
                if not is_compat:
                    raise ValidationError(
                        message=f"Template compatibility check failed: {'; '.join(errors)}",
                        code="INCOMPATIBLE_TEMPLATE_CONFIGURATION",
                    )

        # 3. Validate initial definition if provided
        initial_def = payload.initial_definition or {}
        if initial_def:
            validate_recipe_definition(initial_def)

        new_recipe = Recipe(
            name=payload.name.strip(),
            county_source_id=payload.county_source_id,
            record_type=payload.record_type.lower(),
            ui_template_id=payload.ui_template_id,
            source_configuration_id=payload.source_configuration_id,
            status=RecipeLifecycleStatus.draft.value,
            description=payload.description,
            created_by=user.id,
        )
        self._db.add(new_recipe)
        await self._db.flush()

        # Seed initial version 1
        v1 = RecipeVersion(
            recipe_id=new_recipe.id,
            version_number=1,
            definition=initial_def,
            lifecycle_status=RecipeLifecycleStatus.draft.value,
            is_current=True,
            is_published=False,
            validation_status="untested",
            notes="Initial draft version",
            created_by=user.id,
        )
        self._db.add(v1)
        await self._db.commit()

        return await self.get_recipe(new_recipe.id, user)

    async def get_recipe(self, recipe_id: uuid.UUID, user: User) -> RecipeResponse:
        """Get recipe by ID with all versions."""
        query = (
            select(Recipe)
            .where(Recipe.id == recipe_id)
            .options(selectinload(Recipe.versions))
        )
        result = await self._db.execute(query)
        recipe = result.scalar_one_or_none()
        if recipe is None:
            raise NotFoundError(f"Recipe {recipe_id} not found", code="RECIPE_NOT_FOUND")
        return RecipeResponse.model_validate(recipe)

    async def update_recipe(
        self, recipe_id: uuid.UUID, payload: RecipeUpdateRequest, user: User
    ) -> RecipeResponse:
        """Update top-level recipe metadata."""
        query = select(Recipe).where(Recipe.id == recipe_id)
        recipe = (await self._db.execute(query)).scalar_one_or_none()
        if recipe is None:
            raise NotFoundError(f"Recipe {recipe_id} not found", code="RECIPE_NOT_FOUND")

        if recipe.created_by and recipe.created_by != user.id and not user.is_superuser:
            raise AuthorizationError(
                message="You do not have permission to modify this recipe",
                code="FORBIDDEN_RECIPE",
            )

        update_data = payload.model_dump(exclude_unset=True)
        for k, v in update_data.items():
            setattr(recipe, k, v)

        await self._db.commit()
        return await self.get_recipe(recipe_id, user)

    async def delete_recipe(self, recipe_id: uuid.UUID, user: User) -> None:
        """Delete recipe and its versions."""
        query = select(Recipe).where(Recipe.id == recipe_id)
        recipe = (await self._db.execute(query)).scalar_one_or_none()
        if recipe is None:
            raise NotFoundError(f"Recipe {recipe_id} not found", code="RECIPE_NOT_FOUND")

        if recipe.created_by and recipe.created_by != user.id and not user.is_superuser:
            raise AuthorizationError(
                message="You do not have permission to delete this recipe",
                code="FORBIDDEN_RECIPE",
            )

        await self._db.delete(recipe)
        await self._db.commit()

    # -------------------------------------------------------------------------
    # Recipe Versions & Lifecycle Management
    # -------------------------------------------------------------------------

    async def list_recipe_versions(
        self, recipe_id: uuid.UUID, user: User
    ) -> list[RecipeVersionResponse]:
        """List all versions of a recipe."""
        await self.get_recipe(recipe_id, user)
        result = await self._db.execute(
            select(RecipeVersion)
            .where(RecipeVersion.recipe_id == recipe_id)
            .order_by(RecipeVersion.version_number.desc())
        )
        versions = result.scalars().all()
        return [RecipeVersionResponse.model_validate(v) for v in versions]

    async def create_recipe_version(
        self,
        recipe_id: uuid.UUID,
        payload: RecipeVersionCreateRequest,
        user: User,
    ) -> RecipeVersionResponse:
        """Create a new version for a recipe.

        New versions always start in Draft status.
        """
        recipe_res = await self._db.execute(
            select(Recipe).where(Recipe.id == recipe_id).options(selectinload(Recipe.versions))
        )
        recipe = recipe_res.scalar_one_or_none()
        if recipe is None:
            raise NotFoundError(f"Recipe {recipe_id} not found", code="RECIPE_NOT_FOUND")

        if recipe.created_by and recipe.created_by != user.id and not user.is_superuser:
            raise AuthorizationError("Permission denied", code="FORBIDDEN_RECIPE")

        # Validate definition structure
        if payload.definition:
            validate_recipe_definition(payload.definition)

        # Determine next version number
        highest_v = 0
        for v in recipe.versions:
            if v.version_number > highest_v:
                highest_v = v.version_number
            v.is_current = False

        next_v = highest_v + 1

        new_version = RecipeVersion(
            recipe_id=recipe_id,
            version_number=next_v,
            template_version_id=payload.template_version_id,
            definition=payload.definition,
            lifecycle_status=RecipeLifecycleStatus.draft.value,
            is_current=True,
            is_published=False,
            validation_status="untested",
            notes=payload.notes,
            created_by=user.id,
        )
        self._db.add(new_version)
        recipe.status = RecipeLifecycleStatus.draft.value

        await self._db.commit()
        await self._db.refresh(new_version)
        return RecipeVersionResponse.model_validate(new_version)

    async def get_recipe_version(
        self, recipe_id: uuid.UUID, version_id: uuid.UUID, user: User
    ) -> RecipeVersionResponse:
        """Get a specific recipe version."""
        result = await self._db.execute(
            select(RecipeVersion).where(
                RecipeVersion.id == version_id,
                RecipeVersion.recipe_id == recipe_id,
            )
        )
        v = result.scalar_one_or_none()
        if v is None:
            raise NotFoundError("Recipe version not found", code="VERSION_NOT_FOUND")
        return RecipeVersionResponse.model_validate(v)

    async def update_recipe_version_definition(
        self,
        recipe_id: uuid.UUID,
        version_id: uuid.UUID,
        definition: dict[str, Any],
        notes: str | None,
        user: User,
    ) -> RecipeVersionResponse:
        """Update definition of an un-published recipe version.

        Enforces IMMUTABILITY: Published versions cannot be altered.
        """
        result = await self._db.execute(
            select(RecipeVersion).where(
                RecipeVersion.id == version_id,
                RecipeVersion.recipe_id == recipe_id,
            )
        )
        version = result.scalar_one_or_none()
        if version is None:
            raise NotFoundError("Recipe version not found", code="VERSION_NOT_FOUND")

        # IMMUTABILITY RULE
        if version.is_published:
            raise ConflictError(
                message="Published recipe versions are immutable. Create a new version to apply changes.",
                code="IMMUTABLE_PUBLISHED_VERSION",
            )

        if version.lifecycle_status == RecipeLifecycleStatus.deprecated.value:
            raise ConflictError(
                message="Deprecated recipe versions cannot be edited.",
                code="IMMUTABLE_DEPRECATED_VERSION",
            )

        validate_recipe_definition(definition)
        version.definition = definition
        if notes:
            version.notes = notes

        # When edited, reset validation status to untested
        version.validation_status = "untested"
        if version.lifecycle_status in (
            RecipeLifecycleStatus.validated.value,
            RecipeLifecycleStatus.approved.value,
        ):
            version.lifecycle_status = RecipeLifecycleStatus.draft.value

        await self._db.commit()
        await self._db.refresh(version)
        return RecipeVersionResponse.model_validate(version)

    async def publish_recipe_version(
        self, recipe_id: uuid.UUID, version_id: uuid.UUID, user: User
    ) -> RecipeVersionResponse:
        """Publish a recipe version, making it permanently immutable and ready for execution."""
        recipe_res = await self._db.execute(
            select(Recipe).where(Recipe.id == recipe_id).options(selectinload(Recipe.versions))
        )
        recipe = recipe_res.scalar_one_or_none()
        if recipe is None:
            raise NotFoundError("Recipe not found", code="RECIPE_NOT_FOUND")

        target_v: RecipeVersion | None = None
        for v in recipe.versions:
            if v.id == version_id:
                target_v = v
            else:
                v.is_current = False

        if target_v is None:
            raise NotFoundError("Recipe version not found", code="VERSION_NOT_FOUND")

        # Must have completed validation test or been approved
        if target_v.lifecycle_status not in (
            RecipeLifecycleStatus.validated.value,
            RecipeLifecycleStatus.approved.value,
        ):
            raise ValidationError(
                message=f"Cannot publish recipe version in '{target_v.lifecycle_status}' state. Version must be 'validated' or 'approved' first.",
                code="INVALID_LIFECYCLE_TRANSITION",
            )

        target_v.lifecycle_status = RecipeLifecycleStatus.published.value
        target_v.is_published = True
        target_v.is_current = True
        target_v.published_at = datetime.now(timezone.utc)

        recipe.status = RecipeLifecycleStatus.published.value

        await self._db.commit()
        await self._db.refresh(target_v)
        logger.info(
            "recipe.published",
            recipe_id=str(recipe_id),
            version=target_v.version_number,
            published_by=str(user.id),
        )
        return RecipeVersionResponse.model_validate(target_v)

    async def deprecate_recipe_version(
        self, recipe_id: uuid.UUID, version_id: uuid.UUID, user: User
    ) -> RecipeVersionResponse:
        """Mark a recipe version as deprecated while preserving historical audit logs."""
        result = await self._db.execute(
            select(RecipeVersion).where(
                RecipeVersion.id == version_id,
                RecipeVersion.recipe_id == recipe_id,
            )
        )
        version = result.scalar_one_or_none()
        if version is None:
            raise NotFoundError("Recipe version not found", code="VERSION_NOT_FOUND")

        version.lifecycle_status = RecipeLifecycleStatus.deprecated.value
        await self._db.commit()
        await self._db.refresh(version)
        return RecipeVersionResponse.model_validate(version)

    # -------------------------------------------------------------------------
    # Validation Cases
    # -------------------------------------------------------------------------

    async def list_validation_cases(
        self, recipe_id: uuid.UUID, user: User
    ) -> list[ValidationCaseResponse]:
        """List all validation test cases for a recipe."""
        await self.get_recipe(recipe_id, user)
        result = await self._db.execute(
            select(ValidationCase)
            .where(ValidationCase.recipe_id == recipe_id)
            .order_by(ValidationCase.created_at.desc())
        )
        cases = result.scalars().all()
        return [ValidationCaseResponse.model_validate(c) for c in cases]

    async def create_validation_case(
        self,
        recipe_id: uuid.UUID,
        payload: ValidationCaseCreateRequest,
        user: User,
    ) -> ValidationCaseResponse:
        """Create a validation test case for testing recipe output correctness."""
        await self.get_recipe(recipe_id, user)

        new_case = ValidationCase(
            recipe_id=recipe_id,
            name=payload.name.strip(),
            description=payload.description.strip() if payload.description else None,
            input_data=payload.input_data,
            expected_output=payload.expected_output,
            last_passed=None,
            created_by=user.id,
        )
        self._db.add(new_case)
        await self._db.commit()
        await self._db.refresh(new_case)
        return ValidationCaseResponse.model_validate(new_case)

    async def get_validation_case(
        self, recipe_id: uuid.UUID, case_id: uuid.UUID, user: User
    ) -> ValidationCaseResponse:
        """Get a specific validation test case."""
        result = await self._db.execute(
            select(ValidationCase).where(
                ValidationCase.id == case_id,
                ValidationCase.recipe_id == recipe_id,
            )
        )
        case = result.scalar_one_or_none()
        if case is None:
            raise NotFoundError("Validation case not found", code="CASE_NOT_FOUND")
        return ValidationCaseResponse.model_validate(case)

    async def update_validation_case(
        self,
        recipe_id: uuid.UUID,
        case_id: uuid.UUID,
        payload: ValidationCaseUpdateRequest,
        user: User,
    ) -> ValidationCaseResponse:
        """Update a validation test case."""
        result = await self._db.execute(
            select(ValidationCase).where(
                ValidationCase.id == case_id,
                ValidationCase.recipe_id == recipe_id,
            )
        )
        case = result.scalar_one_or_none()
        if case is None:
            raise NotFoundError("Validation case not found", code="CASE_NOT_FOUND")

        update_data = payload.model_dump(exclude_unset=True)
        for k, v in update_data.items():
            setattr(case, k, v)

        # If updated with last_passed=True, update current recipe version validation status
        if payload.last_passed is not None:
            case.last_run_at = datetime.now(timezone.utc)
            if payload.last_passed is True:
                # Update current version to validated
                v_res = await self._db.execute(
                    select(RecipeVersion).where(
                        RecipeVersion.recipe_id == recipe_id,
                        RecipeVersion.is_current == True,  # noqa: E712
                    )
                )
                curr_v = v_res.scalar_one_or_none()
                if curr_v:
                    curr_v.validation_status = "passed"
                    if curr_v.lifecycle_status in (
                        RecipeLifecycleStatus.draft.value,
                        RecipeLifecycleStatus.testing.value,
                    ):
                        curr_v.lifecycle_status = RecipeLifecycleStatus.validated.value

        await self._db.commit()
        await self._db.refresh(case)
        return ValidationCaseResponse.model_validate(case)

    async def delete_validation_case(
        self, recipe_id: uuid.UUID, case_id: uuid.UUID, user: User
    ) -> None:
        """Delete a validation test case."""
        result = await self._db.execute(
            select(ValidationCase).where(
                ValidationCase.id == case_id,
                ValidationCase.recipe_id == recipe_id,
            )
        )
        case = result.scalar_one_or_none()
        if case is None:
            raise NotFoundError("Validation case not found", code="CASE_NOT_FOUND")

        await self._db.delete(case)
        await self._db.commit()
