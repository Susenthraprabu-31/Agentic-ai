"""Results router — read-only access to AssessorResult and TaxResult."""

import uuid

from fastapi import APIRouter

from app.schemas.result import (
    AssessorResultListResponse,
    AssessorResultResponse,
    TaxResultListResponse,
    TaxResultResponse,
)

router = APIRouter(prefix="/results", tags=["results"])


@router.get("/assessor", response_model=AssessorResultListResponse, summary="List assessor results")
async def list_assessor_results(skip: int = 0, limit: int = 50) -> AssessorResultListResponse:
    return AssessorResultListResponse(items=[], total=0, skip=skip, limit=limit)


@router.get(
    "/assessor/{result_id}",
    response_model=AssessorResultResponse,
    summary="Get assessor result by ID",
)
async def get_assessor_result(result_id: uuid.UUID) -> AssessorResultResponse:
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Assessor result not found")


@router.get("/tax", response_model=TaxResultListResponse, summary="List tax results")
async def list_tax_results(skip: int = 0, limit: int = 50) -> TaxResultListResponse:
    return TaxResultListResponse(items=[], total=0, skip=skip, limit=limit)


@router.get("/tax/{result_id}", response_model=TaxResultResponse, summary="Get tax result by ID")
async def get_tax_result(result_id: uuid.UUID) -> TaxResultResponse:
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Tax result not found")
