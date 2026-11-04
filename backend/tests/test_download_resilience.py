"""Comprehensive test suite for Phase 3D.4 Document & PDF Download Resilience and Range Resumption."""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import os
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.documents.document_discovery import DiscoveredDocument
from app.documents.document_downloader import DocumentDownloader, DownloadResult
from app.documents.document_service import DocumentService
from app.documents.document_storage import DocumentStorage
from app.main import app
from app.models import (
    DocumentArtifact,
    DocumentDownloadAttemptRecord,
    DocumentStatus,
    DownloadAttemptStatus,
    NodeRun,
    RunStatus,
    User,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
)

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

SAMPLE_VALID_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f\n0000000010 00000 n\n0000000053 00000 n\n0000000102 00000 n\n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n168\n%%EOF\n"
)


ALL_TABLES = [
    User.__table__,
    Workflow.__table__,
    WorkflowVersion.__table__,
    WorkflowRun.__table__,
    NodeRun.__table__,
    DocumentArtifact.__table__,
    DocumentDownloadAttemptRecord.__table__,
]


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=ALL_TABLES))
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=ALL_TABLES))


@pytest.fixture
def test_user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest_asyncio.fixture
async def auth_headers(test_user_id: uuid.UUID) -> dict[str, str]:
    async with TestSessionLocal() as session:
        user = User(
            id=test_user_id,
            email=f"test_download_{uuid.uuid4().hex[:6]}@example.com",
            hashed_password=hash_password("ValidPassword123!"),
            full_name="Download Test User",
            is_active=True,
            is_superuser=True,
        )
        session.add(user)
        await session.commit()

    token = create_access_token(subject=str(test_user_id))
    return {"Authorization": f"Bearer {token}"}


def make_mock_client(mock_response):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.stream = MagicMock()
    client.stream.return_value.__aenter__ = AsyncMock(return_value=mock_response)
    client.stream.return_value.__aexit__ = AsyncMock(return_value=None)
    return client


@pytest.mark.asyncio
async def test_download_streaming_and_part_file_creation(tmp_path):
    """Tests that direct downloads stream to disk .part files with correct bytes and SHA-256."""
    temp_dir = str(tmp_path / "downloads")
    with patch.object(settings, "DOCUMENT_TEMP_STORAGE_DIR", temp_dir):
        downloader = DocumentDownloader(run_id="run-1", node_id="node-1")
        artifact_id = uuid.uuid4()

        # Mock HTTP streaming response
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.url = "https://county.gov/records/deed.pdf"
        mock_response.headers = {
            "content-type": "application/pdf",
            "content-disposition": 'attachment; filename="county_deed.pdf"',
            "etag": '"etag-12345"',
        }

        async def fake_iter_bytes(chunk_size=65536):
            yield SAMPLE_VALID_PDF[:50]
            yield SAMPLE_VALID_PDF[50:]

        mock_response.aiter_bytes = fake_iter_bytes

        mock_client = make_mock_client(mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("app.browser.url_validator.URLValidator.validate_url"):

            res = await downloader.download_direct(
                "https://county.gov/records/deed.pdf",
                artifact_id=artifact_id,
            )

            assert res.success is True
            assert res.downloaded_bytes == len(SAMPLE_VALID_PDF)
            assert res.etag == '"etag-12345"'
            assert res.temp_filepath is not None
            assert os.path.exists(res.temp_filepath)
            assert res.temp_filepath.endswith(f"{artifact_id}.part")
            assert res.checksum_sha256 == hashlib.sha256(SAMPLE_VALID_PDF).hexdigest()

            # Clean up
            if os.path.exists(res.temp_filepath):
                os.remove(res.temp_filepath)


@pytest.mark.asyncio
async def test_download_size_boundary_enforcement(tmp_path):
    """Tests that downloads exceeding max_size are rejected and the partial file removed."""
    temp_dir = str(tmp_path / "downloads")
    with patch.object(settings, "DOCUMENT_TEMP_STORAGE_DIR", temp_dir):
        downloader = DocumentDownloader(run_id="run-1", node_id="node-1")
        artifact_id = uuid.uuid4()

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.url = "https://county.gov/large.pdf"
        mock_response.headers = {"content-type": "application/pdf"}

        async def fake_huge_stream(chunk_size=65536):
            # Stream 1000 bytes when max size is 500
            yield b"X" * 300
            yield b"X" * 300
            yield b"X" * 400

        mock_response.aiter_bytes = fake_huge_stream
        mock_client = make_mock_client(mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("app.browser.url_validator.URLValidator.validate_url"):

            res = await downloader.download_direct(
                "https://county.gov/large.pdf",
                max_size=500,
                artifact_id=artifact_id,
            )

            assert res.success is False
            assert "exceeds maximum allowed size" in (res.error_message or "")
            expected_part_file = os.path.join(temp_dir, f"{artifact_id}.part")
            assert not os.path.exists(expected_part_file)


@pytest.mark.asyncio
async def test_http_range_resumption_206(tmp_path):
    """Tests HTTP Range header transmission and appending chunks upon 206 Partial Content."""
    temp_dir = str(tmp_path / "downloads")
    os.makedirs(temp_dir, exist_ok=True)
    part_path = os.path.join(temp_dir, "resume_test.part")

    # Write initial half
    first_half = SAMPLE_VALID_PDF[:60]
    second_half = SAMPLE_VALID_PDF[60:]
    with open(part_path, "wb") as f:
        f.write(first_half)

    with patch.object(settings, "DOCUMENT_TEMP_STORAGE_DIR", temp_dir):
        downloader = DocumentDownloader(run_id="run-1", node_id="node-1")

        mock_response = AsyncMock()
        mock_response.status_code = 206  # Partial Content
        mock_response.url = "https://county.gov/doc.pdf"
        mock_response.headers = {
            "content-type": "application/pdf",
            "content-range": f"bytes 60-{len(SAMPLE_VALID_PDF)-1}/{len(SAMPLE_VALID_PDF)}",
            "etag": '"etag-resume"',
        }

        async def fake_resumed_stream(chunk_size=65536):
            yield second_half

        mock_response.aiter_bytes = fake_resumed_stream
        mock_client = make_mock_client(mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("app.browser.url_validator.URLValidator.validate_url"):

            res = await downloader.download_direct(
                "https://county.gov/doc.pdf",
                resume_temp_path=part_path,
                resume_etag='"etag-resume"',
            )

            assert res.success is True
            assert res.range_resumed is True
            assert res.downloaded_bytes == len(SAMPLE_VALID_PDF)

            # Verify assembled content on disk matches full sample
            with open(part_path, "rb") as final_f:
                assembled = final_f.read()
            assert assembled == SAMPLE_VALID_PDF

            os.remove(part_path)


@pytest.mark.asyncio
async def test_http_range_fallback_on_200(tmp_path):
    """Tests that if server returns 200 instead of 206, the part file is truncated and rewritten from 0."""
    temp_dir = str(tmp_path / "downloads")
    os.makedirs(temp_dir, exist_ok=True)
    part_path = os.path.join(temp_dir, "fallback_test.part")

    # Stale partial content
    with open(part_path, "wb") as f:
        f.write(b"OLD_CORRUPTED_PREFIX_DATA")

    with patch.object(settings, "DOCUMENT_TEMP_STORAGE_DIR", temp_dir):
        downloader = DocumentDownloader(run_id="run-1", node_id="node-1")

        mock_response = AsyncMock()
        mock_response.status_code = 200  # Full response, range ignored
        mock_response.url = "https://county.gov/doc.pdf"
        mock_response.headers = {"content-type": "application/pdf"}

        async def fake_full_stream(chunk_size=65536):
            yield SAMPLE_VALID_PDF

        mock_response.aiter_bytes = fake_full_stream
        mock_client = make_mock_client(mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("app.browser.url_validator.URLValidator.validate_url"):

            res = await downloader.download_direct(
                "https://county.gov/doc.pdf",
                resume_temp_path=part_path,
                resume_etag='"etag-old"',
            )

            assert res.success is True
            assert res.range_resumed is False
            with open(part_path, "rb") as f:
                data = f.read()
            assert data == SAMPLE_VALID_PDF

            os.remove(part_path)


@pytest.mark.asyncio
async def test_atomic_file_promotion_and_validation(tmp_path):
    """Tests that DocumentService atomically promotes part files to storage after successful validation."""
    storage_dir = str(tmp_path / "storage")
    temp_dir = str(tmp_path / "downloads")
    os.makedirs(temp_dir, exist_ok=True)

    with patch.object(settings, "DOCUMENT_STORAGE_DIR", storage_dir), \
         patch.object(settings, "DOCUMENT_TEMP_STORAGE_DIR", temp_dir):

        async with TestSessionLocal() as session:
            doc_service = DocumentService(db=session, storage=DocumentStorage())

            discovered = DiscoveredDocument(
                document_type="deed",
                document_title="Grant Deed",
                document_url="https://county.gov/deed.pdf",
                recording_number="DOC-2026-001",
            )

            downloader = DocumentDownloader()
            # Return successful DownloadResult pointing to a real .part file
            part_path = os.path.join(temp_dir, "test_promote.part")
            with open(part_path, "wb") as f:
                f.write(SAMPLE_VALID_PDF)

            mock_res = DownloadResult(
                success=True,
                data=SAMPLE_VALID_PDF,
                temp_filepath=part_path,
                downloaded_bytes=len(SAMPLE_VALID_PDF),
                total_bytes=len(SAMPLE_VALID_PDF),
                content_type="application/pdf",
                original_filename="Grant Deed.pdf",
                status_code=200,
            )

            with patch.object(downloader, "download_direct", new_callable=AsyncMock) as mock_dl:
                mock_dl.return_value = mock_res
                artifact = await doc_service.process_document_retrieval(
                    discovered=discovered,
                    downloader=downloader,
                )
                await session.commit()

                assert artifact.status == DocumentStatus.validated.value
                assert artifact.file_size == len(SAMPLE_VALID_PDF)
                assert not os.path.exists(part_path)  # Atomically moved!

                # Check promoted file exists in persistent storage
                exists = await doc_service.storage.exists(artifact.storage_key)
                assert exists is True


@pytest.mark.asyncio
async def test_corrupted_pdf_rejection(tmp_path):
    """Tests that corrupt downloads or HTML payloads fail validation and clean up temp files."""
    temp_dir = str(tmp_path / "downloads")
    os.makedirs(temp_dir, exist_ok=True)

    with patch.object(settings, "DOCUMENT_TEMP_STORAGE_DIR", temp_dir):
        async with TestSessionLocal() as session:
            doc_service = DocumentService(db=session)

            discovered = DiscoveredDocument(
                document_type="tax_bill",
                document_title="Tax Bill",
                document_url="https://county.gov/tax.html",
            )

            downloader = DocumentDownloader()
            part_path = os.path.join(temp_dir, "test_corrupt.part")
            html_payload = b"<!DOCTYPE html><html><body>Error: Login Required</body></html>"
            with open(part_path, "wb") as f:
                f.write(html_payload)

            mock_res = DownloadResult(
                success=True,
                data=html_payload,
                temp_filepath=part_path,
                downloaded_bytes=len(html_payload),
                content_type="text/html",
                status_code=200,
            )

            with patch.object(downloader, "download_direct", new_callable=AsyncMock) as mock_dl:
                mock_dl.return_value = mock_res
                artifact = await doc_service.process_document_retrieval(
                    discovered=discovered,
                    downloader=downloader,
                )
                await session.commit()

                assert artifact.status == DocumentStatus.failed.value
                assert "Validation failed" in (artifact.error_message or "")
                assert not os.path.exists(part_path)  # Cleaned up


@pytest.mark.asyncio
async def test_recover_stale_downloads_and_cleanup_temp_files(tmp_path):
    """Tests detection of stale downloading artifacts and cleanup of expired .part files."""
    temp_dir = str(tmp_path / "downloads")
    os.makedirs(temp_dir, exist_ok=True)

    # Create expired part file (mtime 48 hours ago)
    old_part = os.path.join(temp_dir, "old_abandoned.part")
    with open(old_part, "wb") as f:
        f.write(b"abandoned")
    past_mtime = datetime.now(timezone.utc).timestamp() - (48 * 3600)
    os.utime(old_part, (past_mtime, past_mtime))

    with patch.object(settings, "DOCUMENT_TEMP_STORAGE_DIR", temp_dir):
        # 1. Test cleanup
        cleaned = DocumentService.cleanup_orphaned_temp_files(max_age_hours=24)
        assert cleaned == 1
        assert not os.path.exists(old_part)

        # 2. Test stale download recovery
        async with TestSessionLocal() as session:
            stale_art = DocumentArtifact(
                id=uuid.uuid4(),
                document_type="deed",
                original_filename="stale.pdf",
                stored_filename="stale.pdf",
                source_url="https://county.gov/stale.pdf",
                storage_key="unassigned",
                mime_type="application/pdf",
                file_size=0,
                checksum_sha256="",
                status=DocumentStatus.downloading.value,
                download_attempts=1,
                last_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=600),
            )
            session.add(stale_art)
            await session.commit()

            service = DocumentService(db=session)
            recovered = await service.recover_stale_downloads(stale_threshold_seconds=300)
            await session.commit()

            assert recovered == 1
            await session.refresh(stale_art)
            assert stale_art.status == DocumentStatus.retry_pending.value


@pytest.mark.asyncio
async def test_download_apis_and_retry(auth_headers: dict[str, str], test_user_id: uuid.UUID):
    """Tests /executions/{run_id}/download-status and /documents/{id}/retry-download."""
    run_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    async with TestSessionLocal() as session:
        wf = Workflow(name="Test Download WF", owner_id=test_user_id)
        session.add(wf)
        await session.flush()

        wfv = WorkflowVersion(workflow_id=wf.id, version_number=1, definition={})
        session.add(wfv)
        await session.flush()

        run = WorkflowRun(
            id=run_id,
            workflow_id=wf.id,
            workflow_version_id=wfv.id,
            status=RunStatus.RUNNING.value,
            triggered_by=test_user_id,
        )
        session.add(run)

        doc = DocumentArtifact(
            id=doc_id,
            workflow_run_id=run_id,
            document_type="deed",
            original_filename="failed_deed.pdf",
            stored_filename=f"{doc_id}.pdf",
            source_url="https://county.gov/records/deed.pdf",
            storage_key="unassigned",
            mime_type="application/pdf",
            file_size=0,
            checksum_sha256="",
            status=DocumentStatus.failed.value,
            error_message="Network timeout during initial fetch",
            download_attempts=1,
            last_attempt_at=datetime.now(timezone.utc),
        )
        session.add(doc)

        attempt = DocumentDownloadAttemptRecord(
            document_artifact_id=doc_id,
            attempt_number=1,
            status=DownloadAttemptStatus.failed.value,
            downloaded_bytes=0,
            error_message="Network timeout during initial fetch",
        )
        session.add(attempt)
        await session.commit()

    from app.db.session import get_db
    async def override_get_db():
        async with TestSessionLocal() as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Check download status API
            status_resp = await client.get(f"/api/v1/executions/{run_id}/download-status", headers=auth_headers)
            assert status_resp.status_code == 200
            status_data = status_resp.json()
            assert status_data["total_documents"] == 1
            assert status_data["failed"] == 1

            # 2. Check download history API
            hist_resp = await client.get(f"/api/v1/documents/{doc_id}/download-history", headers=auth_headers)
            assert hist_resp.status_code == 200
            hist_data = hist_resp.json()
            assert len(hist_data) == 1
            assert hist_data[0]["status"] == "failed"

            # 3. Test retry-download API
            mock_success_res = DownloadResult(
                success=True,
                data=SAMPLE_VALID_PDF,
                downloaded_bytes=len(SAMPLE_VALID_PDF),
                total_bytes=len(SAMPLE_VALID_PDF),
                content_type="application/pdf",
                status_code=200,
            )
            with patch("app.documents.document_downloader.DocumentDownloader.download_direct", new_callable=AsyncMock) as mock_dl:
                mock_dl.return_value = mock_success_res
                retry_resp = await client.post(f"/api/v1/documents/{doc_id}/retry-download", headers=auth_headers)
                assert retry_resp.status_code == 200
                retry_data = retry_resp.json()
                assert retry_data["status"] == "validated"
                assert retry_data["download_attempts"] == 2
    finally:
        app.dependency_overrides.pop(get_db, None)
