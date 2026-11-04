"""Recipe, RecipeVersion, and ValidationCase ORM models."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class RecordType(str, enum.Enum):
    assessor = "assessor"
    tax = "tax"


class RecipeLifecycleStatus(str, enum.Enum):
    draft = "draft"
    testing = "testing"
    validated = "validated"
    approved = "approved"
    published = "published"
    deprecated = "deprecated"


class Recipe(Base):
    """A named extraction recipe tied to a county source and UI template."""

    __tablename__ = "recipes"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    county_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("county_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    record_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default=RecordType.assessor.value
    )
    ui_template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ui_templates.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_configuration_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_configurations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=RecipeLifecycleStatus.draft.value
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    # Relationships
    county_source: Mapped["CountySource"] = relationship("CountySource")
    ui_template: Mapped["UITemplate | None"] = relationship("UITemplate")
    source_configuration: Mapped["SourceConfiguration | None"] = relationship("SourceConfiguration")

    versions: Mapped[list["RecipeVersion"]] = relationship(
        "RecipeVersion",
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="RecipeVersion.version_number",
    )
    validation_cases: Mapped[list["ValidationCase"]] = relationship(
        "ValidationCase",
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="ValidationCase.created_at",
    )

    def __repr__(self) -> str:
        return f"<Recipe id={self.id} name={self.name!r} status={self.status}>"


class RecipeVersion(Base):
    """Immutable version of a Recipe's extraction definition and steps."""

    __tablename__ = "recipe_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    template_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("template_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Declarative search, locators, extraction rules, field mappings
    definition: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    lifecycle_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=RecipeLifecycleStatus.draft.value
    )
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    validation_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="untested"
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    recipe: Mapped["Recipe"] = relationship("Recipe", back_populates="versions")
    template_version: Mapped["TemplateVersion | None"] = relationship("TemplateVersion")

    def __repr__(self) -> str:
        return f"<RecipeVersion id={self.id} recipe_id={self.recipe_id} v={self.version_number} status={self.lifecycle_status}>"


class ValidationCase(Base):
    """A test case used to validate a recipe's output correctness."""

    __tablename__ = "validation_cases"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="Default Validation Test")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    input_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    expected_output: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    recipe: Mapped["Recipe"] = relationship("Recipe", back_populates="validation_cases")

    def __repr__(self) -> str:
        return f"<ValidationCase id={self.id} recipe_id={self.recipe_id} passed={self.last_passed}>"
