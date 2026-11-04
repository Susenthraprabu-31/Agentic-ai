"""Result schemas for AssessorResult and TaxResult."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class AssessorResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    node_run_id: uuid.UUID
    parcel_number: str | None
    owner_name: str | None
    property_address: str | None
    legal_description: str | None
    assessed_value: Decimal | None
    land_value: Decimal | None
    improvement_value: Decimal | None
    tax_year: int | None
    raw_data: dict
    screenshot_s3_key: str | None
    created_at: datetime


class AssessorResultListResponse(BaseModel):
    items: list[AssessorResultResponse]
    total: int


class TaxResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    node_run_id: uuid.UUID
    parcel_number: str | None
    tax_year: int | None
    amount_due: Decimal | None
    amount_paid: Decimal | None
    due_date: datetime | None
    status: str | None
    raw_data: dict
    screenshot_s3_key: str | None
    created_at: datetime


class TaxResultListResponse(BaseModel):
    items: list[TaxResultResponse]
    total: int
