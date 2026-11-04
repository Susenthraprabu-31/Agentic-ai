"""PDF inspection and metadata extraction using pypdf."""

import io
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pypdf

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PDFMetadata:
    """Extracted metadata and structural properties of a PDF."""
    is_valid: bool
    page_count: int | None = None
    title: str | None = None
    author: str | None = None
    creation_date: datetime | None = None
    modification_date: datetime | None = None
    is_encrypted: bool = False
    error_message: str | None = None


def extract_pdf_metadata(pdf_bytes: bytes) -> PDFMetadata:
    """
    Parses PDF bytes to check structural readability, encryption status, and metadata.
    Does not extract body text or perform OCR.
    """
    if not pdf_bytes:
        return PDFMetadata(is_valid=False, error_message="Empty PDF byte stream.")

    # Check PDF magic bytes
    if not pdf_bytes.startswith(b"%PDF-"):
        return PDFMetadata(is_valid=False, error_message="Missing %PDF- header signature.")

    try:
        stream = io.BytesIO(pdf_bytes)
        reader = pypdf.PdfReader(stream)

        if reader.is_encrypted:
            try:
                # Attempt empty password decryption (common for readable encrypted PDFs)
                decrypted = reader.decrypt("")
                if decrypted == 0:
                    return PDFMetadata(
                        is_valid=True,
                        is_encrypted=True,
                        page_count=len(reader.pages) if not reader.is_encrypted else None,
                        error_message="PDF is password-protected/encrypted.",
                    )
            except Exception:
                return PDFMetadata(
                    is_valid=True,
                    is_encrypted=True,
                    error_message="PDF is password-protected/encrypted.",
                )

        page_count = len(reader.pages)
        title = None
        author = None
        creation_date = None
        modification_date = None

        if reader.metadata:
            meta = reader.metadata
            title = str(meta.title).strip() if meta.title else None
            author = str(meta.author).strip() if meta.author else None

            # Attempt safe date extraction
            if meta.creation_date:
                try:
                    creation_date = meta.creation_date
                except Exception:
                    pass
            if meta.modification_date:
                try:
                    modification_date = meta.modification_date
                except Exception:
                    pass

        return PDFMetadata(
            is_valid=True,
            page_count=page_count,
            title=title,
            author=author,
            creation_date=creation_date,
            modification_date=modification_date,
            is_encrypted=False,
        )

    except pypdf.errors.PdfReadError as exc:
        logger.warning("pypdf read error: %s", exc)
        return PDFMetadata(
            is_valid=False,
            error_message=f"Corrupted PDF structure: {str(exc)}",
        )
    except Exception as exc:
        logger.warning("Unexpected error inspecting PDF metadata: %s", exc)
        return PDFMetadata(
            is_valid=False,
            error_message=f"PDF parsing error: {str(exc)}",
        )

