"""CountySource, UITemplate, TemplateVersion, SourceConfiguration ORM models."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class CountySourceStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"
    pending = "pending"


class ConfigStatus(str, enum.Enum):
    unconfigured = "unconfigured"
    partial = "partial"
    configured = "configured"


class AvailabilityStatus(str, enum.Enum):
    available = "available"
    unavailable = "unavailable"
    restricted = "restricted"
    unknown = "unknown"


class SourceType(str, enum.Enum):
    assessor = "assessor"
    tax = "tax"


class CountySource(Base):
    """A county data source (assessor / tax portal)."""

    __tablename__ = "county_sources"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    county: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    country: Mapped[str] = mapped_column(String(50), nullable=False, default="US")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    assessor_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    tax_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    assessor_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=AvailabilityStatus.unknown.value
    )
    tax_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=AvailabilityStatus.unknown.value
    )

    supported_search_types: Mapped[list] = mapped_column(
        JSON, nullable=False, default=lambda: ["parcel_id", "address", "owner_name"]
    )

    status: Mapped[CountySourceStatus] = mapped_column(
        String(50),
        nullable=False,
        default=CountySourceStatus.active,
    )
    config_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ConfigStatus.unconfigured.value,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    configurations: Mapped[list["SourceConfiguration"]] = relationship(
        "SourceConfiguration",
        back_populates="county_source",
        cascade="all, delete-orphan",
        order_by="SourceConfiguration.created_at",
    )

    def __repr__(self) -> str:
        return f"<CountySource id={self.id} county={self.county!r} state={self.state!r}>"


class UITemplate(Base):
    """A reusable UI automation template."""

    __tablename__ = "ui_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    pattern_type: Mapped[str] = mapped_column(String(100), nullable=False, default="STANDARD_FORM")
    supported_source_types: Mapped[list] = mapped_column(
        JSON, nullable=False, default=lambda: ["assessor", "tax"]
    )
    supported_search_methods: Mapped[list] = mapped_column(
        JSON, nullable=False, default=lambda: ["FORM_SUBMIT", "SPA_INTERACTION", "GET", "POST"]
    )
    required_configuration_fields: Mapped[list] = mapped_column(
        JSON, nullable=False, default=lambda: ["parcel_id"]
    )
    default_config_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")

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
    versions: Mapped[list["TemplateVersion"]] = relationship(
        "TemplateVersion", back_populates="template", cascade="all, delete-orphan", order_by="TemplateVersion.version_number"
    )

    def __repr__(self) -> str:
        return f"<UITemplate id={self.id} name={self.name!r}>"


class TemplateVersion(Base):
    """Immutable version of a UITemplate definition."""

    __tablename__ = "template_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ui_templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    template: Mapped["UITemplate"] = relationship("UITemplate", back_populates="versions")

    def __repr__(self) -> str:
        return f"<TemplateVersion id={self.id} template_id={self.template_id} v={self.version_number}>"


class SourceConfiguration(Base):
    """Website-specific selectors, locators, and settings for Assessor or Tax portals."""

    __tablename__ = "source_configurations"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    county_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("county_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default=SourceType.assessor.value
    )
    website_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    search_page_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    search_method: Mapped[str | None] = mapped_column(String(100), nullable=True, default="FORM_SUBMIT")

    # Structured automation definitions
    search_fields: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    search_button: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result_page: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    field_mappings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    pagination_settings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    navigation_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Associated reusable UI template
    template_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("template_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

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
    county_source: Mapped["CountySource"] = relationship(
        "CountySource", back_populates="configurations"
    )
    template_version: Mapped["TemplateVersion | None"] = relationship("TemplateVersion")

    def __repr__(self) -> str:
        return f"<SourceConfiguration id={self.id} county_source_id={self.county_source_id} type={self.source_type}>"
