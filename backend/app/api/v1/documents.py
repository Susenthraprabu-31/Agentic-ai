"""Documents API router — query and download retrieved document artifacts."""

import io
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_active_user
from app.core.logging import get_logger
from app.db.session import get_db
from app.documents.document_service import DocumentService
from app.models.document_artifact import DocumentArtifact
from app.models.execution import WorkflowRun
from app.models.user import User
from app.schemas.document import (
    DocumentArtifactResponse,
    DocumentDownloadAttemptResponse,
    DocumentListResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=DocumentListResponse, summary="List retrieved documents")
async def list_documents(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
    workflow_run_id: uuid.UUID | None = Query(None, description="Filter by workflow run ID"),
    property_record_id: uuid.UUID | None = Query(None, description="Filter by property record ID"),
    document_type: str | None = Query(None, description="Filter by document type (e.g. deed, tax_bill)"),
    status: str | None = Query(None, description="Filter by status (e.g. validated, failed, duplicate)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> DocumentListResponse:
    """Lists document artifacts owned by or accessible to the authenticated user."""
    query = (
        select(DocumentArtifact)
        .outerjoin(WorkflowRun, DocumentArtifact.workflow_run_id == WorkflowRun.id)
    )

    if not current_user.is_superuser:
        query = query.where(
            (WorkflowRun.triggered_by == current_user.id) | (DocumentArtifact.workflow_run_id.is_(None))
        )

    if workflow_run_id:
        query = query.where(DocumentArtifact.workflow_run_id == workflow_run_id)
    if property_record_id:
        query = query.where(DocumentArtifact.property_record_id == property_record_id)
    if document_type:
        query = query.where(DocumentArtifact.document_type == document_type)
    if status:
        query = query.where(DocumentArtifact.status == status)

    count_stmt = select(func.count()).select_from(query.subquery())
    total_res = await db.execute(count_stmt)
    total = total_res.scalar() or 0

    query = query.order_by(DocumentArtifact.created_at.desc()).offset(skip).limit(limit)
    res = await db.execute(query)
    items = list(res.scalars().all())

    return DocumentListResponse(items=items, total=total)


@router.get("/{document_id}", response_model=DocumentArtifactResponse, summary="Get document metadata")
async def get_document(
    document_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> DocumentArtifactResponse:
    """Retrieves metadata for a specific document artifact."""
    service = DocumentService(db=db)
    try:
        doc = await service.get_document_for_user(document_id, current_user)
        return doc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


@router.get("/{document_id}/download", summary="Download document file")
async def download_document(
    document_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> Response:
    """Downloads the verified binary document payload with proper MIME headers."""
    service = DocumentService(db=db)
    try:
        data, filename, mime_type = await service.get_document_binary_stream(
            document_id=document_id, user=current_user
        )

        return StreamingResponse(
            io.BytesIO(data),
            media_type=mime_type or "application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(data)),
            },
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/{document_id}/view", summary="View document inline")
async def view_document(
    document_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> Response:
    """Streams the document binary payload for inline browser rendering."""
    service = DocumentService(db=db)
    try:
        data, filename, mime_type = await service.get_document_binary_stream(
            document_id=document_id, user=current_user
        )

        return StreamingResponse(
            io.BytesIO(data),
            media_type=mime_type or "application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Content-Length": str(len(data)),
                "Cache-Control": "private, max-age=3600",
            },
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/{document_id}/ocr-analyze", summary="Analyze document with Mistral Document AI OCR")
async def analyze_document_with_ocr(
    document_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict[str, Any]:
    """
    Executes Mistral Document AI OCR directly on the stored PDF or image document,
    returning structured property data, tables, and raw layout markdown.
    """
    service = DocumentService(db=db)
    try:
        data, filename, mime_type = await service.get_document_binary_stream(
            document_id=document_id, user=current_user
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    from app.extraction.mistral_webpage_ocr import get_mistral_webpage_ocr
    ocr_srv = get_mistral_webpage_ocr()
    if not ocr_srv.is_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mistral OCR is not enabled or MISTRAL_API_KEY is not configured.",
        )

    try:
        ocr_result = await ocr_srv.extract_from_document_bytes(
            doc_bytes=data,
            mime_type=mime_type or "application/pdf",
        )
        if not ocr_result:
            return {
                "success": False,
                "document_id": str(document_id),
                "filename": filename,
                "error": "Mistral OCR could not extract text from document.",
            }

        return {
            "success": True,
            "document_id": str(document_id),
            "filename": filename,
            "pages_count": ocr_result.get("pages_count", 1),
            "ocr_model": ocr_result.get("ocr_model", "mistral-ocr-2512"),
            "property_fields": {
                "parcel_number": ocr_result.get("parcel_number") or ocr_result.get("account_number"),
                "account_number": ocr_result.get("account_number"),
                "owner_name": ocr_result.get("owner_name"),
                "property_address": ocr_result.get("property_address"),
                "mailing_address": ocr_result.get("mailing_address"),
                "legal_description": ocr_result.get("legal_description"),
                "property_use": ocr_result.get("property_use"),
                "taxing_district": ocr_result.get("taxing_district"),
                "land_size": ocr_result.get("land_size"),
                "exemptions": ocr_result.get("exemptions"),
                "map_number": ocr_result.get("map_number"),
                "just_value": ocr_result.get("just_value"),
                "assessed_value": ocr_result.get("assessed_value"),
                "taxable_value": ocr_result.get("taxable_value"),
                "subdivision": ocr_result.get("subdivision"),
                "tax_status": ocr_result.get("tax_status"),
                "total_amount_due": ocr_result.get("total_amount_due"),
                "tax_year": ocr_result.get("tax_year"),
            },
            "tables": ocr_result.get("raw_tables", []),
            "sales_history": ocr_result.get("sales_history", []),
            "valuation_history": ocr_result.get("valuation_history", []),
            "key_values": ocr_result.get("all_key_values", {}),
            "markdown": ocr_result.get("raw_markdown", ""),
        }
    except Exception as exc:
        logger.exception("Mistral OCR analysis error for document %s: %s", document_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Mistral OCR analysis failed: {str(exc)}",
        )


@router.get(
    "/{document_id}/download-history",
    response_model=list[DocumentDownloadAttemptResponse],
    summary="Get download attempts history for document",
)
async def get_download_history(
    document_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[DocumentDownloadAttemptResponse]:
    """Retrieves all granular download attempts and range resumption logs for a document."""
    service = DocumentService(db=db)
    try:
        # Authorization check
        await service.get_document_for_user(document_id, current_user)
        history = await service.get_download_history(document_id)
        return [DocumentDownloadAttemptResponse.model_validate(h) for h in history]
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


@router.post(
    "/{document_id}/retry-download",
    response_model=DocumentArtifactResponse,
    summary="Retry downloading an incomplete or failed document",
)
async def retry_download(
    document_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> DocumentArtifactResponse:
    """Triggers an authorized resilient retry download, utilizing HTTP Range headers where supported."""
    service = DocumentService(db=db)
    try:
        await service.get_document_for_user(document_id, current_user)
        updated_doc = await service.retry_document_download(document_id, user=current_user)
        response_model = DocumentArtifactResponse.model_validate(updated_doc)
        await db.commit()
        return response_model
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except Exception as exc:
        logger.exception("Error retrying download for %s: %s", document_id, exc)
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

