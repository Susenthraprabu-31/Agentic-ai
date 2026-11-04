"""Pydantic schemas for DocumentArtifact entities."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class DocumentArtifactResponse(BaseModel):
    """Schema for document artifact metadata responses."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_run_id: uuid.UUID | None = None
    node_run_id: uuid.UUID | None = None
    property_record_id: uuid.UUID | None = None
    document_type: str
    original_filename: str
    stored_filename: str
    source_url: str
    source_page_url: str | None = None
    storage_key: str
    mime_type: str
    file_size: int
    checksum_sha256: str
    page_count: int | None = None
    pdf_title: str | None = None
    pdf_author: str | None = None
    document_date: datetime | None = None
    recording_number: str | None = None
    status: str
    error_message: str | None = None
    is_duplicate: bool
    duplicate_of_id: uuid.UUID | None = None
    etag: str | None = None
    last_modified: str | None = None
    downloaded_bytes: int = 0
    download_attempts: int = 0
    last_attempt_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class DocumentDownloadAttemptResponse(BaseModel):
    """Schema for document download attempt audit records."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_artifact_id: uuid.UUID
    attempt_number: int
    status: str
    downloaded_bytes: int
    total_bytes: int | None = None
    etag: str | None = None
    range_resumed: bool = False
    resumed_from_byte: int = 0
    error_message: str | None = None
    started_at: datetime
    completed_at: datetime | None = None


class DocumentListResponse(BaseModel):
    """Schema for paginated list of document artifacts."""
    items: list[DocumentArtifactResponse]
    total: int

