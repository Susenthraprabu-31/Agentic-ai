"""County service — business logic for CountySource and SourceConfiguration."""

import math
import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.county_source import (
    ConfigStatus,
    CountySource,
    SourceConfiguration,
    TemplateVersion,
)
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

logger = get_logger(__name__)


class CountyService:
    """Service handling all county sources and website-specific automation configurations."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_sources(
        self,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        state: str | None = None,
        status: str | None = None,
        is_active: bool | None = None,
        sort_by: str = "created_at",
        sort_dir: str = "desc",
        user: User | None = None,
    ) -> CountySourceListResponse:
        """Query paginated county sources with filtering and search."""
        query = select(CountySource).options(selectinload(CountySource.configurations))

        # Search filter across county, name, state
        if search:
            pattern = f"%{search.strip().lower()}%"
            query = query.where(
                or_(
                    func.lower(CountySource.name).like(pattern),
                    func.lower(CountySource.county).like(pattern),
                    func.lower(CountySource.state).like(pattern),
                )
            )

        if state and state.upper() != "ALL STATES":
            query = query.where(CountySource.state == state.strip().upper())

        if status and status.lower() != "all statuses":
            query = query.where(CountySource.status == status.strip().lower())

        if is_active is not None:
            query = query.where(CountySource.is_active == is_active)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = (await self._db.execute(count_query)).scalar() or 0

        # Sorting
        sort_column = getattr(CountySource, sort_by, CountySource.created_at)
        if sort_dir.lower() == "asc":
            query = query.order_by(sort_column.asc())
        else:
            query = query.order_by(sort_column.desc())

        # Pagination
        offset = (max(page, 1) - 1) * max(size, 1)
        query = query.offset(offset).limit(size)

        result = await self._db.execute(query)
        sources = result.scalars().all()

        total_pages = math.ceil(total / size) if size > 0 else 1

        items = [CountySourceResponse.model_validate(s) for s in sources]
        return CountySourceListResponse(
            items=items,
            total=total,
            page=page,
            size=size,
            pages=total_pages,
        )

    async def create_source(
        self, payload: CountySourceCreateRequest, user: User
    ) -> CountySourceResponse:
        """Create a new county source."""
        # Check duplicate state + county combination
        norm_county = payload.county.strip().lower()
        norm_state = payload.state.strip().upper()

        dup_check = await self._db.execute(
            select(CountySource).where(
                func.lower(CountySource.county) == norm_county,
                CountySource.state == norm_state,
            )
        )
        if dup_check.scalar_one_or_none() is not None:
            raise ConflictError(
                message=f"County source for {payload.county}, {norm_state} already exists",
                code="DUPLICATE_COUNTY_SOURCE",
            )

        new_source = CountySource(
            name=payload.name,
            state=norm_state,
            county=payload.county,
            country=payload.country,
            description=payload.description,
            assessor_url=payload.assessor_url,
            tax_url=payload.tax_url,
            assessor_status=payload.assessor_status,
            tax_status=payload.tax_status,
            supported_search_types=payload.supported_search_types,
            status=payload.status,
            config_status=ConfigStatus.unconfigured.value,
            is_active=payload.is_active,
            notes=payload.notes,
            created_by=user.id,
        )

        self._db.add(new_source)
        await self._db.commit()

        # Reload with configurations
        return await self.get_source(new_source.id, user)

    async def get_source(self, source_id: uuid.UUID, user: User) -> CountySourceResponse:
        """Retrieve a specific county source with all attached configurations."""
        query = (
            select(CountySource)
            .where(CountySource.id == source_id)
            .options(selectinload(CountySource.configurations))
        )
        result = await self._db.execute(query)
        source = result.scalar_one_or_none()

        if source is None:
            raise NotFoundError(
                message=f"County source with ID {source_id} not found",
                code="COUNTY_SOURCE_NOT_FOUND",
            )

        return CountySourceResponse.model_validate(source)

    async def update_source(
        self, source_id: uuid.UUID, payload: CountySourceUpdateRequest, user: User
    ) -> CountySourceResponse:
        """Update a county source with authorization check."""
        query = (
            select(CountySource)
            .where(CountySource.id == source_id)
            .options(selectinload(CountySource.configurations))
        )
        result = await self._db.execute(query)
        source = result.scalar_one_or_none()

        if source is None:
            raise NotFoundError(
                message=f"County source with ID {source_id} not found",
                code="COUNTY_SOURCE_NOT_FOUND",
            )

        # Authorization: owner or superuser
        if source.created_by and source.created_by != user.id and not user.is_superuser:
            raise AuthorizationError(
                message="You do not have permission to modify this county source",
                code="FORBIDDEN_COUNTY_SOURCE",
            )

        # Check for conflict if renaming state/county
        if payload.county or payload.state:
            new_county = (payload.county or source.county).strip().lower()
            new_state = (payload.state or source.state).strip().upper()
            conflict = await self._db.execute(
                select(CountySource).where(
                    func.lower(CountySource.county) == new_county,
                    CountySource.state == new_state,
                    CountySource.id != source_id,
                )
            )
            if conflict.scalar_one_or_none() is not None:
                raise ConflictError(
                    message=f"County source for {new_county}, {new_state} already exists",
                    code="DUPLICATE_COUNTY_SOURCE",
                )

        update_data = payload.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(source, field, value)

        await self._db.commit()
        await self._db.refresh(source)
        return CountySourceResponse.model_validate(source)

    async def delete_source(self, source_id: uuid.UUID, user: User) -> None:
        """Delete a county source with authorization check."""
        result = await self._db.execute(select(CountySource).where(CountySource.id == source_id))
        source = result.scalar_one_or_none()

        if source is None:
            raise NotFoundError(
                message=f"County source with ID {source_id} not found",
                code="COUNTY_SOURCE_NOT_FOUND",
            )

        if source.created_by and source.created_by != user.id and not user.is_superuser:
            raise AuthorizationError(
                message="You do not have permission to delete this county source",
                code="FORBIDDEN_COUNTY_SOURCE",
            )

        await self._db.delete(source)
        await self._db.commit()
        logger.info("county_source.deleted", source_id=str(source_id), deleted_by=str(user.id))

    # -------------------------------------------------------------------------
    # Source Configurations (Assessor / Tax)
    # -------------------------------------------------------------------------

    async def list_configurations(
        self, source_id: uuid.UUID, user: User
    ) -> list[SourceConfigResponse]:
        """List all configurations for a given county source."""
        # Ensure source exists
        await self.get_source(source_id, user)

        result = await self._db.execute(
            select(SourceConfiguration)
            .where(SourceConfiguration.county_source_id == source_id)
            .order_by(SourceConfiguration.created_at.desc())
        )
        configs = result.scalars().all()
        return [SourceConfigResponse.model_validate(c) for c in configs]

    async def create_configuration(
        self,
        source_id: uuid.UUID,
        payload: SourceConfigCreateRequest,
        user: User,
    ) -> SourceConfigResponse:
        """Create website-specific configuration for a county source."""
        source_query = (
            select(CountySource)
            .where(CountySource.id == source_id)
            .options(selectinload(CountySource.configurations))
        )
        source = (await self._db.execute(source_query)).scalar_one_or_none()

        if source is None:
            raise NotFoundError(
                message=f"County source with ID {source_id} not found",
                code="COUNTY_SOURCE_NOT_FOUND",
            )

        if source.created_by and source.created_by != user.id and not user.is_superuser:
            raise AuthorizationError(
                message="You do not have permission to add configurations to this source",
                code="FORBIDDEN_COUNTY_SOURCE",
            )

        # Validate template_version_id if supplied
        if payload.template_version_id:
            tv_check = await self._db.execute(
                select(TemplateVersion).where(TemplateVersion.id == payload.template_version_id)
            )
            if tv_check.scalar_one_or_none() is None:
                raise NotFoundError(
                    message=f"Template version {payload.template_version_id} not found",
                    code="TEMPLATE_VERSION_NOT_FOUND",
                )

        new_config = SourceConfiguration(
            county_source_id=source_id,
            source_type=payload.source_type,
            website_url=payload.website_url,
            search_page_url=payload.search_page_url,
            search_method=payload.search_method,
            search_fields=payload.search_fields,
            search_button=payload.search_button,
            result_page=payload.result_page,
            field_mappings=payload.field_mappings,
            pagination_settings=payload.pagination_settings,
            navigation_config=payload.navigation_config,
            template_version_id=payload.template_version_id,
            status=payload.status,
            is_active=payload.is_active,
            created_by=user.id,
        )

        self._db.add(new_config)

        # Update parent county source's config_status
        await self._db.flush()
        await self._recalculate_source_status(source)

        await self._db.commit()
        await self._db.refresh(new_config)

        logger.info(
            "source_config.created",
            config_id=str(new_config.id),
            source_id=str(source_id),
            source_type=payload.source_type,
        )
        return SourceConfigResponse.model_validate(new_config)

    async def get_configuration(
        self, source_id: uuid.UUID, config_id: uuid.UUID, user: User
    ) -> SourceConfigResponse:
        """Retrieve a specific configuration."""
        result = await self._db.execute(
            select(SourceConfiguration).where(
                SourceConfiguration.id == config_id,
                SourceConfiguration.county_source_id == source_id,
            )
        )
        config = result.scalar_one_or_none()
        if config is None:
            raise NotFoundError(
                message=f"Configuration with ID {config_id} not found for this county source",
                code="CONFIGURATION_NOT_FOUND",
            )
        return SourceConfigResponse.model_validate(config)

    async def update_configuration(
        self,
        source_id: uuid.UUID,
        config_id: uuid.UUID,
        payload: SourceConfigUpdateRequest,
        user: User,
    ) -> SourceConfigResponse:
        """Update an existing configuration."""
        result = await self._db.execute(
            select(SourceConfiguration).where(
                SourceConfiguration.id == config_id,
                SourceConfiguration.county_source_id == source_id,
            )
        )
        config = result.scalar_one_or_none()
        if config is None:
            raise NotFoundError(
                message=f"Configuration with ID {config_id} not found",
                code="CONFIGURATION_NOT_FOUND",
            )

        if config.created_by and config.created_by != user.id and not user.is_superuser:
            raise AuthorizationError(
                message="You do not have permission to update this configuration",
                code="FORBIDDEN_CONFIGURATION",
            )

        if payload.template_version_id:
            tv_check = await self._db.execute(
                select(TemplateVersion).where(TemplateVersion.id == payload.template_version_id)
            )
            if tv_check.scalar_one_or_none() is None:
                raise NotFoundError(
                    message=f"Template version {payload.template_version_id} not found",
                    code="TEMPLATE_VERSION_NOT_FOUND",
                )

        update_data = payload.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(config, field, value)

        await self._db.commit()
        await self._db.refresh(config)
        return SourceConfigResponse.model_validate(config)

    async def delete_configuration(
        self, source_id: uuid.UUID, config_id: uuid.UUID, user: User
    ) -> None:
        """Delete an existing configuration."""
        result = await self._db.execute(
            select(SourceConfiguration).where(
                SourceConfiguration.id == config_id,
                SourceConfiguration.county_source_id == source_id,
            )
        )
        config = result.scalar_one_or_none()
        if config is None:
            raise NotFoundError(
                message=f"Configuration with ID {config_id} not found",
                code="CONFIGURATION_NOT_FOUND",
            )

        if config.created_by and config.created_by != user.id and not user.is_superuser:
            raise AuthorizationError(
                message="You do not have permission to delete this configuration",
                code="FORBIDDEN_CONFIGURATION",
            )

        await self._db.delete(config)
        await self._db.flush()

        # Recalculate config_status
        source_query = (
            select(CountySource)
            .where(CountySource.id == source_id)
            .options(selectinload(CountySource.configurations))
        )
        source = (await self._db.execute(source_query)).scalar_one_or_none()
        if source:
            await self._recalculate_source_status(source)

        await self._db.commit()
        logger.info("source_config.deleted", config_id=str(config_id), source_id=str(source_id))

    async def _recalculate_source_status(self, source: CountySource) -> None:
        """Update county source's config_status based on configurations present."""
        query = select(SourceConfiguration.source_type).where(
            SourceConfiguration.county_source_id == source.id,
            SourceConfiguration.is_active == True,  # noqa: E712
        )
        result = await self._db.execute(query)
        types = set(result.scalars().all())

        if "assessor" in types and "tax" in types:
            source.config_status = ConfigStatus.configured.value
        elif len(types) > 0:
            source.config_status = ConfigStatus.partial.value
        else:
            source.config_status = ConfigStatus.unconfigured.value
