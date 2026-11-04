"""Recipe loader and configuration resolver for county website automation."""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.browser.url_validator import URLValidator
from app.core.logging import get_logger
from app.models.county_source import SourceConfiguration, TemplateVersion
from app.models.recipe import Recipe, RecipeLifecycleStatus, RecipeVersion

logger = get_logger(__name__)


class RecipeLoadException(Exception):
    """Raised when loading or validating a recipe version fails."""
    pass


@dataclass
class ResolvedRecipe:
    """Fully resolved and validated recipe ready for execution."""
    recipe_id: uuid.UUID | None
    recipe_version_id: uuid.UUID | None
    recipe_name: str
    record_type: str  # assessor or tax
    website_url: str
    search_page_url: str
    search_method: str  # FORM_SUBMIT, SPA_INTERACTION, GET, POST
    steps: list[dict[str, Any]] = field(default_factory=list)
    extract_schema: dict[str, Any] = field(default_factory=dict)
    pagination_settings: dict[str, Any] = field(default_factory=dict)
    table_settings: dict[str, Any] = field(default_factory=dict)
    source_configuration_id: uuid.UUID | None = None
    detail_page_config: dict[str, Any] = field(default_factory=dict)


class RecipeLoader:
    """
    Retrieves and validates approved, published recipe versions and website configurations.
    Enforces strict version matching: never silently substitutes other versions.
    """

    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db

    async def load_recipe_for_execution(
        self,
        node_data: dict[str, Any],
        record_type: str = "assessor",
        county_source_id: uuid.UUID | str | None = None,
    ) -> ResolvedRecipe:
        """
        Loads the exact recipe version and website configuration configured for a workflow node.
        Rejects unpublished or deprecated versions.
        """
        # 1. Check if fully resolved declarative steps and extract schema are directly passed (e.g. testing)
        direct_steps = node_data.get("recipe_steps") or node_data.get("steps")
        if direct_steps and isinstance(direct_steps, list):
            search_url = node_data.get("website_url") or node_data.get("search_page_url") or ""
            if search_url:
                URLValidator.validate_url(search_url)

            return ResolvedRecipe(
                recipe_id=None,
                recipe_version_id=None,
                recipe_name=node_data.get("recipe_name", f"{record_type}_direct_recipe"),
                record_type=record_type,
                website_url=search_url,
                search_page_url=node_data.get("search_page_url", search_url),
                search_method=node_data.get("search_method", "FORM_SUBMIT"),
                steps=direct_steps,
                extract_schema=node_data.get("extract_schema") or node_data.get("field_mappings") or {},
                pagination_settings=node_data.get("pagination_settings") or node_data.get("pagination") or {},
                table_settings=node_data.get("table_settings") or node_data.get("result_page") or {},
                detail_page_config=node_data.get("detail_page_config") or {},
            )

        if not self.db:
            raise RecipeLoadException(
                "Database session required to load RecipeVersion or SourceConfiguration from database."
            )

        # 2. Extract identifiers from node configuration
        recipe_version_id_str = node_data.get("recipe_version_id")
        recipe_id_str = node_data.get("recipe_id")
        source_config_id_str = node_data.get("source_configuration_id")

        recipe_version: RecipeVersion | None = None
        source_config: SourceConfiguration | None = None

        # 3. Load exact RecipeVersion if specified
        if recipe_version_id_str:
            try:
                r_ver_uuid = uuid.UUID(str(recipe_version_id_str))
            except ValueError as e:
                raise RecipeLoadException(f"Invalid recipe_version_id UUID: {recipe_version_id_str}") from e

            stmt = (
                select(RecipeVersion)
                .where(RecipeVersion.id == r_ver_uuid)
                .options(
                    selectinload(RecipeVersion.recipe).selectinload(Recipe.source_configuration),
                    selectinload(RecipeVersion.template_version),
                )
            )
            res = await self.db.execute(stmt)
            recipe_version = res.scalar_one_or_none()

            if not recipe_version:
                logger.warning(
                    "RecipeVersion %s not found in database. Will attempt fallback to latest published recipe for county.",
                    r_ver_uuid,
                )
            else:
                if not recipe_version.is_published:
                    raise RecipeLoadException(
                        f"RecipeVersion {recipe_version.id} (v{recipe_version.version_number}) is not published. "
                        f"Status: {recipe_version.lifecycle_status}. Production runs require published recipes."
                    )

                if recipe_version.lifecycle_status == RecipeLifecycleStatus.deprecated.value:
                    raise RecipeLoadException(
                        f"RecipeVersion {recipe_version.id} (v{recipe_version.version_number}) is deprecated."
                    )

                # Check if a newer published version of the same recipe is available (e.g. upgraded by AI analyzer)
                latest_stmt = (
                    select(RecipeVersion)
                    .where(
                        RecipeVersion.recipe_id == recipe_version.recipe_id,
                        RecipeVersion.is_published == True,
                    )
                    .options(
                        selectinload(RecipeVersion.recipe).selectinload(Recipe.source_configuration),
                        selectinload(RecipeVersion.template_version),
                    )
                    .order_by(RecipeVersion.version_number.desc())
                )
                latest_res = await self.db.execute(latest_stmt)
                latest_ver = latest_res.scalars().first()
                if latest_ver and latest_ver.version_number > recipe_version.version_number:
                    logger.info(
                        "Auto-resolving to latest published recipe version %s (v%d) for recipe %s (supersedes v%d)",
                        latest_ver.id, latest_ver.version_number, latest_ver.recipe_id, recipe_version.version_number
                    )
                    recipe_version = latest_ver

                # Retrieve source configuration from recipe or explicit override
                if recipe_version.recipe and recipe_version.recipe.source_configuration:
                    source_config = recipe_version.recipe.source_configuration

        # 4. If recipe_id given without version, load the published version
        elif recipe_id_str:
            try:
                r_uuid = uuid.UUID(str(recipe_id_str))
            except ValueError as e:
                raise RecipeLoadException(f"Invalid recipe_id UUID: {recipe_id_str}") from e

            stmt = (
                select(RecipeVersion)
                .join(Recipe, Recipe.id == RecipeVersion.recipe_id)
                .where(RecipeVersion.recipe_id == r_uuid, RecipeVersion.is_published == True)
                .options(
                    selectinload(RecipeVersion.recipe).selectinload(Recipe.source_configuration),
                    selectinload(RecipeVersion.template_version),
                )
                .order_by(RecipeVersion.version_number.desc())
            )
            res = await self.db.execute(stmt)
            recipe_version = res.scalars().first()

            if not recipe_version:
                raise RecipeLoadException(
                    f"No approved, published RecipeVersion found for Recipe {r_uuid}."
                )

            if recipe_version.recipe and recipe_version.recipe.source_configuration:
                source_config = recipe_version.recipe.source_configuration

        # 5. Load explicit SourceConfiguration if provided or override
        if source_config_id_str:
            try:
                sc_uuid = uuid.UUID(str(source_config_id_str))
            except ValueError as e:
                raise RecipeLoadException(f"Invalid source_configuration_id UUID: {source_config_id_str}") from e

            stmt = (
                select(SourceConfiguration)
                .where(SourceConfiguration.id == sc_uuid)
                .options(selectinload(SourceConfiguration.template_version))
            )
            res = await self.db.execute(stmt)
            loaded_sc = res.scalar_one_or_none()
            if not loaded_sc:
                raise RecipeLoadException(f"SourceConfiguration {sc_uuid} not found.")
            source_config = loaded_sc

        # 5.5 If county_source_id is provided without explicit recipe_id or source_configuration_id
        effective_cs_id = (
            county_source_id
            or node_data.get("county_source_id")
        )
        if not recipe_version and not source_config and effective_cs_id:
            try:
                cs_uuid = uuid.UUID(str(effective_cs_id))
            except ValueError as e:
                raise RecipeLoadException(f"Invalid county_source_id UUID: {effective_cs_id}") from e

            # First, attempt to load an approved, published RecipeVersion for this county and record_type
            stmt = (
                select(RecipeVersion)
                .join(Recipe, Recipe.id == RecipeVersion.recipe_id)
                .where(
                    Recipe.county_source_id == cs_uuid,
                    Recipe.record_type == record_type,
                    RecipeVersion.is_published == True,
                )
                .options(
                    selectinload(RecipeVersion.recipe).selectinload(Recipe.source_configuration),
                    selectinload(RecipeVersion.template_version),
                )
                .order_by(RecipeVersion.version_number.desc())
            )
            res = await self.db.execute(stmt)
            recipe_version = res.scalars().first()

            if recipe_version and recipe_version.recipe and recipe_version.recipe.source_configuration:
                source_config = recipe_version.recipe.source_configuration

            # If no published recipe version exists yet, look for active SourceConfiguration
            if not source_config:
                sc_stmt = (
                    select(SourceConfiguration)
                    .where(
                        SourceConfiguration.county_source_id == cs_uuid,
                        SourceConfiguration.source_type == record_type,
                        SourceConfiguration.is_active == True,
                    )
                    .options(selectinload(SourceConfiguration.template_version))
                )
                sc_res = await self.db.execute(sc_stmt)
                source_config = sc_res.scalar_one_or_none()

        # 6. Validate configuration existence and active status
        if not recipe_version and not source_config:
            raise RecipeLoadException(
                f"Node {node_data.get('id', 'unknown')} has neither recipe_version_id, source_configuration_id, nor county_source_id configured."
            )

        if source_config and not source_config.is_active:
            raise RecipeLoadException(f"SourceConfiguration {source_config.id} is inactive.")

        # 7. Validate template compatibility
        if recipe_version and source_config:
            if (
                recipe_version.template_version_id
                and source_config.template_version_id
                and recipe_version.template_version_id != source_config.template_version_id
            ):
                logger.warning(
                    "TemplateVersion mismatch: RecipeVersion has %s, SourceConfiguration has %s",
                    recipe_version.template_version_id,
                    source_config.template_version_id,
                )

        # 8. Resolve URLs, steps, and extraction schemas
        website_url = (
            (source_config.website_url if source_config else None)
            or node_data.get("website_url")
            or ""
        )
        search_page_url = (
            (source_config.search_page_url if source_config else None)
            or node_data.get("search_page_url")
            or website_url
        )
        search_method = (
            (source_config.search_method if source_config else None)
            or node_data.get("search_method")
            or "FORM_SUBMIT"
        )

        # Validate URL for SSRF protection
        if website_url:
            URLValidator.validate_url(website_url)
        if search_page_url and search_page_url != website_url:
            URLValidator.validate_url(search_page_url)

        definition = recipe_version.definition if recipe_version else {}
        steps = list(definition.get("steps", []))

        # If no steps defined on recipe_version, construct steps from template + source config
        if not steps and source_config:
            steps = self._synthesize_steps_from_config(source_config, search_page_url)

        extract_schema = (
            definition.get("field_mappings")
            or (source_config.field_mappings if source_config else {})
            or node_data.get("extract_schema")
            or {}
        )

        pagination_settings = (
            definition.get("pagination")
            or (source_config.pagination_settings if source_config else {})
            or node_data.get("pagination_settings")
            or {}
        )

        table_settings = (
            definition.get("table_settings")
            or (source_config.result_page if source_config else {})
            or node_data.get("table_settings")
            or {}
        )

        detail_page_config = (
            definition.get("detail_page_config")
            or (source_config.navigation_config.get("detail_page") if source_config and source_config.navigation_config else {})
            or node_data.get("detail_page_config")
            or {}
        )

        recipe_name = (
            recipe_version.recipe.name
            if (recipe_version and recipe_version.recipe)
            else f"{record_type}_source_config_{source_config.id if source_config else 'custom'}"
        )

        return ResolvedRecipe(
            recipe_id=recipe_version.recipe_id if recipe_version else None,
            recipe_version_id=recipe_version.id if recipe_version else None,
            recipe_name=recipe_name,
            record_type=record_type,
            website_url=website_url,
            search_page_url=search_page_url,
            search_method=search_method,
            steps=steps,
            extract_schema=extract_schema,
            pagination_settings=pagination_settings,
            table_settings=table_settings,
            source_configuration_id=source_config.id if source_config else None,
            detail_page_config=detail_page_config,
        )

    def _synthesize_steps_from_config(
        self, source_config: SourceConfiguration, search_page_url: str
    ) -> list[dict[str, Any]]:
        """
        Synthesizes standard search steps from SourceConfiguration fields when explicit steps
        are not defined in a RecipeVersion.
        """
        steps: list[dict[str, Any]] = [
            {"action": "navigate", "url": search_page_url or "{{search_url}}"}
        ]

        # Fill search fields
        fields = source_config.search_fields or {}
        for param_name, field_def in fields.items():
            selector = (
                field_def.get("selector")
                or field_def.get("locator", {}).get("value")
                or field_def.get("id")
            )
            if selector:
                steps.append({
                    "action": "fill",
                    "selector": selector,
                    "value": f"{{{{input.{param_name}}}}}",
                })

        # Click search button
        button_def = source_config.search_button or {}
        button_sel = (
            button_def.get("selector")
            or button_def.get("locator", {}).get("value")
            or button_def.get("id")
        )
        if button_sel:
            steps.append({"action": "click", "selector": button_sel})
            steps.append({"action": "wait", "duration": 1000})

        return steps

