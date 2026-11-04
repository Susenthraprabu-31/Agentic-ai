"""Data models for property records, queries, and extracted data."""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class QueryType(str, Enum):
    PARCEL = "parcel"
    ADDRESS = "address"
    OWNER = "owner"
    BOOK_PAGE = "book_page"


class ParcelRecord(BaseModel):
    apn: Optional[str] = None
    owner_name: Optional[str] = None
    legal_desc: Optional[str] = None
    legal_description: Optional[str] = None
    assessed_value: Optional[float] = None
    just_value: Optional[float] = None
    property_address: Optional[str] = None
    source: str = "assessor"
    raw_json: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.legal_desc and not self.legal_description:
            self.legal_description = self.legal_desc
        elif self.legal_description and not self.legal_desc:
            self.legal_desc = self.legal_description
        if not self.apn and self.raw_json.get("parcel_number"):
            self.apn = str(self.raw_json.get("parcel_number"))

