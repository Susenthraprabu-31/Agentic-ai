"""AI Celery tasks — Mistral Document AI OCR and Structured Normalization."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.extraction.mistral_webpage_ocr import get_mistral_webpage_ocr
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="ocr_screenshot", bind=True, max_retries=2)
def ocr_screenshot(self, node_run_id: str, s3_key: str) -> dict[str, Any]:
    """Run Mistral OCR on a screenshot stored in persistent storage or local file.

    1. Reads the screenshot image bytes.
    2. Calls Mistral Document AI OCR.
    3. Returns normalized property attributes and markdown.
    """
    try:
        # Check if s3_key is a local filepath or storage key
        image_bytes: bytes | None = None
        p = Path(s3_key)
        if p.is_file():
            image_bytes = p.read_bytes()
        else:
            from app.documents.document_storage import DocumentStorageService
            srv = DocumentStorageService()
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                image_bytes = loop.run_until_complete(srv.retrieve(s3_key))
            finally:
                loop.close()

        if not image_bytes:
            logger.warning("Image payload not found for OCR screenshot: %s", s3_key)
            return {"node_run_id": node_run_id, "success": False, "error": f"Image file not found: {s3_key}"}

        ocr_service = get_mistral_webpage_ocr()
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(
                ocr_service.extract_from_image_bytes(image_bytes, mime_type="image/png")
            )
        finally:
            loop.close()

        return {
            "node_run_id": node_run_id,
            "success": True,
            "result": result,
        }
    except Exception as exc:
        logger.error("Error in Celery task ocr_screenshot: %s", exc)
        raise self.retry(exc=exc, countdown=5)


@celery_app.task(name="normalize_result", bind=True, max_retries=2)
def normalize_result(self, node_run_id: str, raw_data: dict) -> dict:
    """Use OpenAI to normalise and structure raw scraped data.

    Phase 2 implementation.
    """
    raise NotImplementedError("Phase 2")
