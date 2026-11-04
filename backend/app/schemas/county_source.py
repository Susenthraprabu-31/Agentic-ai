"""CountySource and SourceConfiguration request/response schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.county_source import CountySourceStatus


class SourceConfigCreateRequest(BaseModel):
    """Payload to create a new source configuration (Assessor or Tax)."""

    source_type: str = Field(..., description="'assessor' or 'tax'")
    website_url: str = Field(..., min_length=1, max_length=2048)
    search_page_url: str | None = None
    search_method: str | None = "FORM_SUBMIT"

    # Automation definitions
    search_fields: dict[str, Any] = Field(default_factory=dict)
    search_button: dict[str, Any] = Field(default_factory=dict)
    result_page: dict[str, Any] = Field(default_factory=dict)
    field_mappings: dict[str, Any] = Field(default_factory=dict)
    pagination_settings: dict[str, Any] = Field(default_factory=dict)
    navigation_config: dict[str, Any] = Field(default_factory=dict)

    template_version_id: uuid.UUID | None = None
    status: str = "draft"
    is_active: bool = True

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, v: str) -> str:
        clean = v.strip().lower()
        if clean not in ("assessor", "tax"):
            raise ValueError("source_type must be either 'assessor' or 'tax'")
        return clean


class SourceConfigUpdateRequest(BaseModel):
    """Payload to update an existing source configuration."""

    website_url: str | None = None
    search_page_url: str | None = None
    search_method: str | None = None

    search_fields: dict[str, Any] | None = None
    search_button: dict[str, Any] | None = None
    result_page: dict[str, Any] | None = None
    field_mappings: dict[str, Any] | None = None
    pagination_settings: dict[str, Any] | None = None
    navigation_config: dict[str, Any] | None = None

    template_version_id: uuid.UUID | None = None
    status: str | None = None
    is_active: bool | None = None


class SourceConfigResponse(BaseModel):
    """Public representation of a website configuration."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    county_source_id: uuid.UUID
    source_type: str
    website_url: str
    search_page_url: str | None
    search_method: str | None

    search_fields: dict[str, Any]
    search_button: dict[str, Any]
    result_page: dict[str, Any]
    field_mappings: dict[str, Any]
    pagination_settings: dict[str, Any]
    navigation_config: dict[str, Any]

    template_version_id: uuid.UUID | None
    status: str
    is_active: bool
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime | None


class CountySourceCreateRequest(BaseModel):
    """Payload to create a new CountySource."""

    name: str = Field(..., min_length=1, max_length=255)
    state: str = Field(..., min_length=2, max_length=2)
    county: str = Field(..., min_length=1, max_length=255)
    country: str = "US"
    description: str | None = None

    assessor_url: str | None = None
    tax_url: str | None = None
    assessor_status: str = "unknown"
    tax_status: str = "unknown"

    supported_search_types: list[str] = Field(
        default_factory=lambda: ["parcel_id", "address", "owner_name"]
    )
    status: CountySourceStatus = CountySourceStatus.active
    is_active: bool = True
    notes: str | None = None

    @field_validator("state")
    @classmethod
    def normalize_state(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("county", "name")
    @classmethod
    def clean_text(cls, v: str) -> str:
        return v.strip()


class CountySourceUpdateRequest(BaseModel):
    """Payload to update an existing CountySource."""

    name: str | None = None
    state: str | None = None
    county: str | None = None
    country: str | None = None
    description: str | None = None

    assessor_url: str | None = None
    tax_url: str | None = None
    assessor_status: str | None = None
    tax_status: str | None = None

    supported_search_types: list[str] | None = None
    status: CountySourceStatus | None = None
    config_status: str | None = None
    is_active: bool | None = None
    notes: str | None = None

    @field_validator("state")
    @classmethod
    def normalize_state(cls, v: str | None) -> str | None:
        return v.strip().upper() if v else None


class CountySourceResponse(BaseModel):
    """Full representation of a CountySource."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    state: str
    county: str
    country: str
    description: str | None

    assessor_url: str | None
    tax_url: str | None
    assessor_status: str
    tax_status: str
    supported_search_types: list[str]

    status: CountySourceStatus
    config_status: str
    is_active: bool
    notes: str | None

    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime | None

    configurations: list[SourceConfigResponse] = Field(default_factory=list)


class CountySourceListResponse(BaseModel):
    """Paginated list of county sources."""

    items: list[CountySourceResponse]
    total: int
    page: int
    size: int
    pages: int
