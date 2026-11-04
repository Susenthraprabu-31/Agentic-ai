"""High-level document retrieval orchestration service with download resilience, Range resumption, and lifecycle audit."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import time
import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.documents.document_deduplicator import DocumentDeduplicator
from app.documents.document_discovery import DiscoveredDocument, DocumentDiscovery
from app.documents.document_downloader import DocumentDownloader, DownloadResult
from app.documents.document_storage import DocumentStorage, sanitize_filename
from app.documents.document_validator import DocumentValidator, ValidationOutcome
from app.models.document_artifact import DocumentArtifact, DocumentStatus
from app.models.document_download_attempt import DocumentDownloadAttemptRecord, DownloadAttemptStatus
from app.models.execution import WorkflowRun
from app.models.user import User

logger = get_logger(__name__)


class DocumentService:
    """
    Coordinates document discovery, downloading, HTTP Range resumption, validation, deduplication,
    atomic storage promotion, and database tracking.
    """

    def __init__(
        self,
        db: AsyncSession | None = None,
        storage: DocumentStorage | None = None,
    ) -> None:
        self.db = db
        self.storage = storage or DocumentStorage()

    async def process_document_retrieval(
        self,
        discovered: DiscoveredDocument,
        downloader: DocumentDownloader,
        workflow_run_id: uuid.UUID | None = None,
        node_run_id: uuid.UUID | None = None,
        property_record_id: uuid.UUID | None = None,
        source_configuration_id: uuid.UUID | None = None,
    ) -> DocumentArtifact:
        """
        Executes download, validation, deduplication, and storage for a single discovered document.
        Does not raise on failure; captures failure state cleanly in DocumentArtifact and audit record.
        """
        doc_id = uuid.uuid4()
        original_filename = sanitize_filename(discovered.document_title or "document.pdf")
        if not original_filename.lower().endswith(".pdf"):
            original_filename += ".pdf"

        # 0. Idempotency Check: if document was already retrieved and validated for this run, reuse it
        if self.db and workflow_run_id and discovered.document_url:
            existing_q = select(DocumentArtifact).where(
                DocumentArtifact.workflow_run_id == workflow_run_id,
                DocumentArtifact.source_url == discovered.document_url,
                DocumentArtifact.status.in_([
                    DocumentStatus.validated.value,
                    DocumentStatus.completed.value,
                    DocumentStatus.duplicate.value,
                ]),
            )
            existing_res = await self.db.execute(existing_q)
            existing_doc = existing_res.scalar_one_or_none()
            if existing_doc:
                logger.info(
                    "Document already retrieved for run %s and url %s. Reusing artifact %s.",
                    workflow_run_id,
                    discovered.document_url,
                    existing_doc.id,
                )
                return existing_doc

        now = datetime.now(timezone.utc)

        # 1. Initialize Document Artifact in downloading state
        artifact = DocumentArtifact(
            id=doc_id,
            workflow_run_id=workflow_run_id,
            node_run_id=node_run_id,
            property_record_id=property_record_id,
            source_configuration_id=source_configuration_id,
            document_type=discovered.document_type,
            original_filename=original_filename,
            stored_filename=f"{doc_id}.pdf",
            source_url=discovered.document_url or "",
            source_page_url=discovered.source_page_url,
            storage_key="unassigned",
            mime_type="application/pdf",
            file_size=0,
            checksum_sha256="",
            recording_number=discovered.recording_number,
            status=DocumentStatus.downloading.value,
            download_attempts=1,
            last_attempt_at=now,
        )
        if self.db:
            self.db.add(artifact)
            await self.db.flush()

        # Record download attempt
        attempt_record = DocumentDownloadAttemptRecord(
            document_artifact_id=doc_id,
            attempt_number=1,
            status=DownloadAttemptStatus.in_progress.value,
            downloaded_bytes=0,
            started_at=now,
        )
        if self.db:
            self.db.add(attempt_record)
            await self.db.flush()

        # 2. Download document with streaming and part file support
        if discovered.button_selector:
            download_res = await downloader.download_via_browser_event(
                discovered.button_selector,
                artifact_id=doc_id,
            )
        elif discovered.document_url:
            download_res = await downloader.download_direct(
                discovered.document_url,
                referer=discovered.source_page_url,
                artifact_id=doc_id,
            )
        else:
            download_res = None

        complete_time = datetime.now(timezone.utc)

        # If download failed
        if not download_res or not download_res.success:
            err = download_res.error_message if download_res else "No valid URL or trigger element."
            artifact.status = DocumentStatus.failed.value
            artifact.error_message = err

            attempt_record.status = DownloadAttemptStatus.failed.value
            attempt_record.error_message = err
            attempt_record.completed_at = complete_time
            if self.db:
                await self.db.flush()
            return artifact

        # Download succeeded: update attempt record
        attempt_record.status = DownloadAttemptStatus.completed.value
        attempt_record.downloaded_bytes = download_res.downloaded_bytes
        attempt_record.total_bytes = download_res.total_bytes
        attempt_record.etag = download_res.etag
        attempt_record.range_resumed = download_res.range_resumed
        attempt_record.completed_at = complete_time

        artifact.status = DocumentStatus.validating.value
        artifact.etag = download_res.etag
        artifact.last_modified = download_res.last_modified
        artifact.downloaded_bytes = download_res.downloaded_bytes
        artifact.temp_filepath = download_res.temp_filepath
        artifact.source_url = download_res.final_url or discovered.document_url or ""
        actual_filename = sanitize_filename(download_res.original_filename or original_filename)
        artifact.original_filename = actual_filename

        data = download_res.data or b""

        # 3. Validate document
        val_res = DocumentValidator.validate_document(
            data, content_type=download_res.content_type
        )

        if not val_res.is_valid:
            artifact.status = DocumentStatus.failed.value
            artifact.mime_type = val_res.mime_type
            artifact.file_size = val_res.file_size
            artifact.checksum_sha256 = download_res.checksum_sha256 or (DocumentDeduplicator.compute_sha256(data) if data else "")
            artifact.error_message = f"Validation failed ({val_res.outcome.value}): {val_res.error_message}"

            # Clean up temp file upon corruption or validation failure
            if download_res.temp_filepath and os.path.exists(download_res.temp_filepath):
                try:
                    os.remove(download_res.temp_filepath)
                except OSError:
                    pass
            if self.db:
                await self.db.flush()
            return artifact

        # 4. Deduplication Check
        dedup_res = await DocumentDeduplicator.check_duplicate(
            data=data, db=self.db, workflow_run_id=workflow_run_id
        )

        if dedup_res.is_duplicate and dedup_res.existing_storage_key:
            storage_key = dedup_res.existing_storage_key
            is_dup = True
            dup_id = dedup_res.duplicate_of_id
            status = DocumentStatus.duplicate.value
            # Remove temp file since content already exists
            if download_res.temp_filepath and os.path.exists(download_res.temp_filepath):
                try:
                    os.remove(download_res.temp_filepath)
                except OSError:
                    pass
        else:
            storage_key = DocumentStorage.generate_storage_key(
                workflow_run_id=workflow_run_id,
                property_record_id=property_record_id,
                document_id=doc_id,
            )
            # Atomically promote temp part file into persistent storage
            if download_res.temp_filepath and os.path.exists(download_res.temp_filepath):
                await self.storage.promote_file(download_res.temp_filepath, storage_key)
            else:
                await self.storage.save(storage_key, data)

            is_dup = False
            dup_id = None
            status = DocumentStatus.validated.value

        pdf_meta = val_res.pdf_metadata

        artifact.storage_key = storage_key
        artifact.mime_type = val_res.mime_type
        artifact.file_size = val_res.file_size
        artifact.checksum_sha256 = dedup_res.checksum_sha256
        artifact.page_count = pdf_meta.page_count if pdf_meta else None
        artifact.pdf_title = pdf_meta.title if pdf_meta else None
        artifact.pdf_author = pdf_meta.author if pdf_meta else None
        artifact.status = status
        artifact.is_duplicate = is_dup
        artifact.duplicate_of_id = dup_id
        artifact.temp_filepath = None  # Cleared after promotion

        if self.db:
            await self.db.flush()
            await self.db.refresh(artifact)

        return artifact

    async def retry_document_download(
        self,
        document_id: uuid.UUID,
        user: User | None = None,
    ) -> DocumentArtifact:
        """
        Manually or automatically retries downloading a failed or incomplete document artifact,
        reusing partial .part files with HTTP Range when available.
        """
        if not self.db:
            raise ValueError("Database session required for retry.")

        stmt = select(DocumentArtifact).where(DocumentArtifact.id == document_id)
        res = await self.db.execute(stmt)
        doc = res.scalar_one_or_none()
        if not doc:
            raise LookupError(f"Document artifact {document_id} not found.")

        # If already validated or duplicate, return immediately
        if doc.status in (DocumentStatus.validated.value, DocumentStatus.completed.value, DocumentStatus.duplicate.value):
            return doc

        now = datetime.now(timezone.utc)
        attempt_number = doc.download_attempts + 1
        doc.download_attempts = attempt_number
        doc.status = DocumentStatus.downloading.value
        doc.last_attempt_at = now
        doc.error_message = None

        # Check if existing partial file exists
        resume_temp_path = None
        if doc.temp_filepath and os.path.exists(doc.temp_filepath):
            resume_temp_path = doc.temp_filepath
        else:
            temp_dir = os.path.abspath(settings.DOCUMENT_TEMP_STORAGE_DIR)
            candidate = os.path.join(temp_dir, f"{doc.id}.part")
            if os.path.exists(candidate):
                resume_temp_path = candidate

        existing_bytes = os.path.getsize(resume_temp_path) if resume_temp_path else 0

        attempt_record = DocumentDownloadAttemptRecord(
            document_artifact_id=doc.id,
            attempt_number=attempt_number,
            status=DownloadAttemptStatus.in_progress.value,
            downloaded_bytes=existing_bytes,
            resumed_from_byte=existing_bytes,
            started_at=now,
        )
        self.db.add(attempt_record)
        await self.db.flush()

        downloader = DocumentDownloader(run_id=str(doc.workflow_run_id or "retry"), node_id="retry_node")
        download_res = await downloader.download_direct(
            doc.source_url,
            referer=doc.source_page_url,
            artifact_id=doc.id,
            resume_temp_path=resume_temp_path,
            resume_etag=doc.etag,
        )

        complete_time = datetime.now(timezone.utc)
        if not download_res or not download_res.success:
            err = download_res.error_message if download_res else "Retry failed."
            doc.status = DocumentStatus.failed.value
            doc.error_message = err
            attempt_record.status = DownloadAttemptStatus.failed.value
            attempt_record.error_message = err
            attempt_record.completed_at = complete_time
            await self.db.flush()
            return doc

        attempt_record.status = DownloadAttemptStatus.completed.value
        attempt_record.downloaded_bytes = download_res.downloaded_bytes
        attempt_record.total_bytes = download_res.total_bytes
        attempt_record.etag = download_res.etag
        attempt_record.range_resumed = download_res.range_resumed
        attempt_record.completed_at = complete_time

        data = download_res.data or b""
        val_res = DocumentValidator.validate_document(data, content_type=download_res.content_type)
        if not val_res.is_valid:
            doc.status = DocumentStatus.failed.value
            doc.error_message = f"Validation failed ({val_res.outcome.value}): {val_res.error_message}"
            if download_res.temp_filepath and os.path.exists(download_res.temp_filepath):
                try:
                    os.remove(download_res.temp_filepath)
                except OSError:
                    pass
            await self.db.flush()
            return doc

        # Deduplication & promotion
        dedup_res = await DocumentDeduplicator.check_duplicate(data=data, db=self.db, workflow_run_id=doc.workflow_run_id)
        if dedup_res.is_duplicate and dedup_res.existing_storage_key:
            doc.storage_key = dedup_res.existing_storage_key
            doc.is_duplicate = True
            doc.duplicate_of_id = dedup_res.duplicate_of_id
            doc.status = DocumentStatus.duplicate.value
            if download_res.temp_filepath and os.path.exists(download_res.temp_filepath):
                try:
                    os.remove(download_res.temp_filepath)
                except OSError:
                    pass
        else:
            storage_key = DocumentStorage.generate_storage_key(
                workflow_run_id=doc.workflow_run_id,
                property_record_id=doc.property_record_id,
                document_id=doc.id,
            )
            if download_res.temp_filepath and os.path.exists(download_res.temp_filepath):
                await self.storage.promote_file(download_res.temp_filepath, storage_key)
            else:
                await self.storage.save(storage_key, data)

            doc.storage_key = storage_key
            doc.status = DocumentStatus.validated.value

        pdf_meta = val_res.pdf_metadata
        doc.file_size = val_res.file_size
        doc.checksum_sha256 = dedup_res.checksum_sha256
        doc.page_count = pdf_meta.page_count if pdf_meta else None
        doc.pdf_title = pdf_meta.title if pdf_meta else None
        doc.pdf_author = pdf_meta.author if pdf_meta else None
        doc.temp_filepath = None

        await self.db.flush()
        await self.db.refresh(doc)
        return doc

    async def get_download_history(
        self,
        document_id: uuid.UUID,
    ) -> list[DocumentDownloadAttemptRecord]:
        """Retrieves all historical download attempts for a document artifact."""
        if not self.db:
            return []

        stmt = (
            select(DocumentDownloadAttemptRecord)
            .where(DocumentDownloadAttemptRecord.document_artifact_id == document_id)
            .order_by(DocumentDownloadAttemptRecord.attempt_number.asc())
        )
        res = await self.db.execute(stmt)
        return list(res.scalars().all())

    async def get_download_status_for_execution(
        self,
        workflow_run_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Aggregates document download status and metrics for a workflow execution."""
        if not self.db:
            return {}

        stmt = select(DocumentArtifact).where(DocumentArtifact.workflow_run_id == workflow_run_id)
        res = await self.db.execute(stmt)
        docs = list(res.scalars().all())

        total_discovered = len(docs)
        completed = sum(1 for d in docs if d.status in (DocumentStatus.validated.value, DocumentStatus.completed.value))
        failed = sum(1 for d in docs if d.status == DocumentStatus.failed.value)
        downloading = sum(1 for d in docs if d.status in (DocumentStatus.downloading.value, DocumentStatus.queued.value))
        duplicates = sum(1 for d in docs if d.status == DocumentStatus.duplicate.value)
        total_bytes = sum(d.file_size for d in docs if d.file_size)

        return {
            "workflow_run_id": str(workflow_run_id),
            "total_documents": total_discovered,
            "completed": completed,
            "failed": failed,
            "in_progress": downloading,
            "duplicate": duplicates,
            "total_bytes_stored": total_bytes,
            "documents": [
                {
                    "id": str(d.id),
                    "filename": d.original_filename,
                    "status": d.status,
                    "file_size": d.file_size,
                    "download_attempts": d.download_attempts,
                    "is_duplicate": d.is_duplicate,
                    "error_message": d.error_message,
                }
                for d in docs
            ],
        }

    async def recover_stale_downloads(self, stale_threshold_seconds: int = 300) -> int:
        """
        Detects downloads stuck in 'downloading' status beyond stale threshold
        and resets them for safe resumption.
        """
        if not self.db:
            return 0

        threshold_time = datetime.now(timezone.utc) - timedelta(seconds=stale_threshold_seconds)
        stmt = select(DocumentArtifact).where(
            DocumentArtifact.status == DocumentStatus.downloading.value,
            DocumentArtifact.last_attempt_at < threshold_time,
        )
        res = await self.db.execute(stmt)
        stale_docs = list(res.scalars().all())

        recovered_count = 0
        for doc in stale_docs:
            if doc.download_attempts < settings.DOCUMENT_DOWNLOAD_MAX_ATTEMPTS:
                doc.status = DocumentStatus.retry_pending.value
                doc.error_message = f"Stale download detected (> {stale_threshold_seconds}s). Scheduled for retry."
            else:
                doc.status = DocumentStatus.failed.value
                doc.error_message = f"Download timed out after {doc.download_attempts} attempts."
            recovered_count += 1

        if recovered_count > 0:
            await self.db.flush()
            logger.info("Recovered %d stale document downloads.", recovered_count)

        return recovered_count

    @staticmethod
    def cleanup_orphaned_temp_files(max_age_hours: int = 24) -> int:
        """
        Removes temporary .part files from DOCUMENT_TEMP_STORAGE_DIR older than max_age_hours.
        """
        temp_dir = os.path.abspath(settings.DOCUMENT_TEMP_STORAGE_DIR)
        if not os.path.exists(temp_dir):
            return 0

        now = time.time()
        max_age_seconds = max_age_hours * 3600
        removed_count = 0

        try:
            for fname in os.listdir(temp_dir):
                if not fname.endswith(".part"):
                    continue
                fpath = os.path.join(temp_dir, fname)
                try:
                    stat = os.stat(fpath)
                    if (now - stat.st_mtime) > max_age_seconds:
                        os.remove(fpath)
                        removed_count += 1
                except Exception as e:
                    logger.warning("Error inspecting/removing temp file %s: %s", fpath, e)
        except Exception as e:
            logger.warning("Error during temp file cleanup: %s", e)

        if removed_count > 0:
            logger.info("Cleaned up %d orphaned temporary download files.", removed_count)
        return removed_count

    async def get_document_for_user(
        self,
        document_id: uuid.UUID,
        user: User,
    ) -> DocumentArtifact:
        """
        Retrieves document artifact enforcing ownership and authorization.
        """
        if not self.db:
            raise ValueError("Database session required.")

        stmt = (
            select(DocumentArtifact)
            .where(DocumentArtifact.id == document_id)
            .options(selectinload(DocumentArtifact.workflow_run))
        )
        res = await self.db.execute(stmt)
        doc = res.scalar_one_or_none()
        if not doc:
            raise LookupError(f"Document {document_id} not found.")

        if doc.workflow_run and doc.workflow_run.triggered_by != user.id and not getattr(user, "is_superuser", False):
            raise PermissionError("Access denied: You do not have permission to view this document.")

        return doc

    async def get_document_binary_stream(
        self,
        document_id: uuid.UUID,
        user: User,
    ) -> tuple[bytes, str, str]:
        """
        Retrieves file bytes, filename, and MIME type for authenticated download.
        """
        doc = await self.get_document_for_user(document_id, user)
        if doc.status == DocumentStatus.failed.value:
            raise ValueError(f"Document download failed: {doc.error_message}")

        data = await self.storage.retrieve(doc.storage_key)
        if data is None and doc.storage_key:
            # Resilient fallback: locate file if stored under relative tmp/ or local paths
            raw_key = doc.storage_key.strip()
            fn = doc.original_filename or Path(raw_key).name
            candidates: list[Path] = [
                Path(raw_key),
                Path(settings.DOCUMENT_STORAGE_DIR) / raw_key,
                Path("backend") / settings.DOCUMENT_STORAGE_DIR / raw_key,
                Path("backend/storage/documents") / raw_key,
                Path("storage/documents") / raw_key,
                Path("backend") / raw_key,
                Path("tmp") / raw_key,
                Path.cwd() / raw_key,
            ]
            if fn:
                try:
                    candidates.extend(Path("tmp").glob(f"**/{fn}"))
                    candidates.extend(Path("backend/tmp").glob(f"**/{fn}"))
                    candidates.extend(Path("storage").glob(f"**/{fn}"))
                    candidates.extend(Path("backend/storage").glob(f"**/{fn}"))
                except Exception:
                    pass

            for cand in candidates:
                try:
                    if cand.is_file() and cand.stat().st_size > 0:
                        data = cand.read_bytes()
                        logger.info("Retrieved document binary from fallback path: %s (%d bytes)", cand, len(data))
                        # Persist permanently to avoid missing file in future
                        try:
                            persistent_key = DocumentStorage.generate_storage_key(
                                doc.workflow_run_id, doc.property_record_id, doc.id, extension="pdf"
                            )
                            await self.storage.save(persistent_key, data)
                            doc.storage_key = persistent_key
                            if self.db:
                                await self.db.commit()
                        except Exception as persist_err:
                            logger.debug("Document auto-persistence note: %s", persist_err)
                        break
                except Exception:
                    continue

        if data is None:
            raise LookupError("Document file is missing from persistent storage.")

        return data, doc.original_filename, doc.mime_type

    async def list_documents_for_execution(
        self,
        workflow_run_id: uuid.UUID,
        user: User,
    ) -> list[DocumentArtifact]:
        """
        Lists all document artifacts associated with a workflow execution run.
        """
        if not self.db:
            return []

        run_stmt = select(WorkflowRun).where(WorkflowRun.id == workflow_run_id)
        run_res = await self.db.execute(run_stmt)
        run = run_res.scalar_one_or_none()
        if not run:
            raise LookupError(f"Workflow execution {workflow_run_id} not found.")

        if run.triggered_by != user.id and not getattr(user, "is_superuser", False):
            raise PermissionError("Access denied: You do not own this workflow run.")

        doc_stmt = (
            select(DocumentArtifact)
            .where(DocumentArtifact.workflow_run_id == workflow_run_id)
            .order_by(DocumentArtifact.created_at.asc())
        )
        doc_res = await self.db.execute(doc_stmt)
        return list(doc_res.scalars().all())
