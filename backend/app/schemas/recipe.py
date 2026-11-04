"""Recipe, UI Template, and ValidationCase Pydantic schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# UI Template Schemas
# ---------------------------------------------------------------------------


class UITemplateCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    pattern_type: str = "STANDARD_FORM"
    supported_source_types: list[str] = Field(default_factory=lambda: ["assessor", "tax"])
    supported_search_methods: list[str] = Field(
        default_factory=lambda: ["FORM_SUBMIT", "SPA_INTERACTION", "GET", "POST"]
    )
    required_configuration_fields: list[str] = Field(default_factory=lambda: ["parcel_id"])
    default_config_schema: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    initial_definition: dict[str, Any] | None = None


class UITemplateUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    pattern_type: str | None = None
    supported_source_types: list[str] | None = None
    supported_search_methods: list[str] | None = None
    required_configuration_fields: list[str] | None = None
    default_config_schema: dict[str, Any] | None = None
    tags: list[str] | None = None
    status: str | None = None


class TemplateVersionCreateRequest(BaseModel):
    definition: dict[str, Any] = Field(default_factory=dict)
    changelog: str | None = None
    is_current: bool = False


class TemplateVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    template_id: uuid.UUID
    version_number: int
    definition: dict[str, Any]
    changelog: str | None
    is_current: bool
    created_by: uuid.UUID | None
    created_at: datetime


class UITemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    pattern_type: str
    supported_source_types: list[str]
    supported_search_methods: list[str]
    required_configuration_fields: list[str]
    default_config_schema: dict[str, Any]
    tags: list[str]
    status: str
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime | None
    versions: list[TemplateVersionResponse] = Field(default_factory=list)


class UITemplateListResponse(BaseModel):
    items: list[UITemplateResponse]
    total: int
    page: int
    size: int
    pages: int


# ---------------------------------------------------------------------------
# Recipe Schemas
# ---------------------------------------------------------------------------


class RecipeCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    county_source_id: uuid.UUID
    record_type: str = Field("assessor", description="'assessor' or 'tax'")
    ui_template_id: uuid.UUID | None = None
    source_configuration_id: uuid.UUID | None = None
    description: str | None = None
    initial_definition: dict[str, Any] | None = None


class RecipeUpdateRequest(BaseModel):
    name: str | None = None
    ui_template_id: uuid.UUID | None = None
    source_configuration_id: uuid.UUID | None = None
    description: str | None = None
    status: str | None = None


class RecipeVersionCreateRequest(BaseModel):
    definition: dict[str, Any] = Field(default_factory=dict)
    template_version_id: uuid.UUID | None = None
    notes: str | None = None


class RecipeVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recipe_id: uuid.UUID
    version_number: int
    template_version_id: uuid.UUID | None
    definition: dict[str, Any]
    lifecycle_status: str
    is_current: bool
    is_published: bool
    validation_status: str
    notes: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    published_at: datetime | None


class RecipeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    county_source_id: uuid.UUID
    record_type: str
    ui_template_id: uuid.UUID | None
    source_configuration_id: uuid.UUID | None
    status: str
    description: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime | None

    versions: list[RecipeVersionResponse] = Field(default_factory=list)


class RecipeListResponse(BaseModel):
    items: list[RecipeResponse]
    total: int
    page: int
    size: int
    pages: int


# ---------------------------------------------------------------------------
# Validation Cases
# ---------------------------------------------------------------------------


class ValidationCaseCreateRequest(BaseModel):
    name: str = Field("Default Test", min_length=1, max_length=255)
    description: str | None = None
    input_data: dict[str, Any] = Field(default_factory=dict)
    expected_output: dict[str, Any] = Field(default_factory=dict)


class ValidationCaseUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    input_data: dict[str, Any] | None = None
    expected_output: dict[str, Any] | None = None
    last_passed: bool | None = None
    last_error: str | None = None


class ValidationCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recipe_id: uuid.UUID
    name: str
    description: str | None
    input_data: dict[str, Any]
    expected_output: dict[str, Any]
    last_run_at: datetime | None
    last_passed: bool | None
    last_error: str | None
    created_by: uuid.UUID | None
    created_at: datetime


class CompatibilityCheckResponse(BaseModel):
    is_compatible: bool
    errors: list[str] = Field(default_factory=list)
