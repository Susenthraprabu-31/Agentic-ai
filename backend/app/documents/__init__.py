"""Document and PDF retrieval package."""

from app.documents.document_deduplicator import DeduplicationResult, DocumentDeduplicator
from app.documents.document_discovery import DiscoveredDocument, DocumentDiscovery
from app.documents.document_downloader import DocumentDownloader, DownloadResult
from app.documents.document_service import DocumentService
from app.documents.document_storage import DocumentStorage, LocalDocumentStorage, sanitize_filename
from app.documents.document_validator import DocumentValidator, ValidationOutcome, ValidationResult
from app.documents.pdf_metadata import PDFMetadata, extract_pdf_metadata

__all__ = [
    "DeduplicationResult",
    "DiscoveredDocument",
    "DocumentDeduplicator",
    "DocumentDiscovery",
    "DocumentDownloader",
    "DocumentService",
    "DocumentStorage",
    "DocumentValidator",
    "DownloadResult",
    "LocalDocumentStorage",
    "PDFMetadata",
    "ValidationOutcome",
    "ValidationResult",
    "extract_pdf_metadata",
    "sanitize_filename",
]

