"""Document and PDF validation engine."""

import enum
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.documents.pdf_metadata import PDFMetadata, extract_pdf_metadata

logger = get_logger(__name__)


class ValidationOutcome(str, enum.Enum):
    VALID_PDF = "VALID_PDF"
    NON_PDF_RESPONSE = "NON_PDF_RESPONSE"
    CORRUPTED_PDF = "CORRUPTED_PDF"
    EMPTY_FILE = "EMPTY_FILE"
    ENCRYPTED_PDF = "ENCRYPTED_PDF"
    OVERSIZED_PDF = "OVERSIZED_PDF"
    UNSUPPORTED_TYPE = "UNSUPPORTED_TYPE"
    DOWNLOAD_TIMEOUT = "DOWNLOAD_TIMEOUT"
    ACCESS_DENIED = "ACCESS_DENIED"


@dataclass
class ValidationResult:
    """Outcome of document validation with structural metadata."""
    is_valid: bool
    outcome: ValidationOutcome
    mime_type: str = "application/pdf"
    file_size: int = 0
    error_message: str | None = None
    pdf_metadata: PDFMetadata | None = None


class DocumentValidator:
    """
    Validates downloaded document binaries against PDF signatures, MIME types,
    structural integrity, and size constraints. Never misclassifies HTML responses as PDFs.
    """

    PDF_MAGIC_BYTES = b"%PDF-"
    MAX_FILE_SIZE = getattr(settings, "DOCUMENT_MAX_FILE_SIZE_BYTES", 26214400)  # 25 MB

    @classmethod
    def validate_document(
        cls,
        data: bytes,
        content_type: str | None = None,
        max_size: int | None = None,
        expected_type: str = "pdf",
    ) -> ValidationResult:
        """
        Performs thorough validation of downloaded document bytes.
        """
        max_bytes = max_size or cls.MAX_FILE_SIZE

        # 1. Empty Check
        if not data or len(data) == 0:
            return ValidationResult(
                is_valid=False,
                outcome=ValidationOutcome.EMPTY_FILE,
                file_size=0,
                error_message="Downloaded document is empty (0 bytes).",
            )

        file_size = len(data)

        # 2. Oversized Check
        if file_size > max_bytes:
            return ValidationResult(
                is_valid=False,
                outcome=ValidationOutcome.OVERSIZED_PDF,
                file_size=file_size,
                error_message=f"Document size ({file_size} bytes) exceeds limit ({max_bytes} bytes).",
            )

        # 3. Content-Type Check (Detect HTML error or login pages)
        ct_lower = (content_type or "").lower().strip()
        if "text/html" in ct_lower or "application/xhtml+xml" in ct_lower:
            return ValidationResult(
                is_valid=False,
                outcome=ValidationOutcome.NON_PDF_RESPONSE,
                mime_type="text/html",
                file_size=file_size,
                error_message="Server returned an HTML page instead of a document (e.g. login or error page).",
            )

        # 4. Check for Non-PDF HTML/XML payload in raw bytes
        stripped_prefix = data[:1024].lstrip().lower()
        if (
            stripped_prefix.startswith(b"<!doctype html")
            or stripped_prefix.startswith(b"<html")
            or stripped_prefix.startswith(b"<?xml")
        ):
            return ValidationResult(
                is_valid=False,
                outcome=ValidationOutcome.NON_PDF_RESPONSE,
                mime_type="text/html",
                file_size=file_size,
                error_message="Raw file content starts with HTML/XML markup rather than document signature.",
            )

        # 5. PDF Signature Check
        if expected_type.lower() == "pdf":
            if not data.startswith(cls.PDF_MAGIC_BYTES):
                return ValidationResult(
                    is_valid=False,
                    outcome=ValidationOutcome.CORRUPTED_PDF,
                    file_size=file_size,
                    error_message="Missing %PDF- file header signature.",
                )

            # 6. PDF Structural Inspection
            meta = extract_pdf_metadata(data)

            if not meta.is_valid:
                return ValidationResult(
                    is_valid=False,
                    outcome=ValidationOutcome.CORRUPTED_PDF,
                    file_size=file_size,
                    error_message=meta.error_message or "PDF structural validation failed.",
                    pdf_metadata=meta,
                )

            if meta.is_encrypted:
                return ValidationResult(
                    is_valid=False,
                    outcome=ValidationOutcome.ENCRYPTED_PDF,
                    mime_type="application/pdf",
                    file_size=file_size,
                    error_message=meta.error_message or "PDF is password-protected/encrypted.",
                    pdf_metadata=meta,
                )

            return ValidationResult(
                is_valid=True,
                outcome=ValidationOutcome.VALID_PDF,
                mime_type="application/pdf",
                file_size=file_size,
                pdf_metadata=meta,
            )

        # Unsupported or other document types
        return ValidationResult(
            is_valid=False,
            outcome=ValidationOutcome.UNSUPPORTED_TYPE,
            file_size=file_size,
            error_message=f"Unsupported expected document type '{expected_type}'.",
        )
