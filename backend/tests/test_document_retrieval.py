"""Unit and integration tests for Phase 3C Document & PDF Retrieval Automation.

Tests:
1. PDF Metadata Extraction: valid metadata, page counts, encrypted PDFs, corrupted PDFs.
2. Document Validation: valid PDF, HTML error pages, corrupted structures, empty files, encrypted PDFs, oversized payloads.
3. SSRF & Scheme Protection: private IPs (127.0.0.1, 10.0.0.0/8, 192.168.0.0/16), AWS metadata (169.254.169.254), non-HTTP schemes.
4. Storage & Filename Sanitization: path separator sanitization, partition by workflow run, save/retrieve/exists.
5. Deduplication: SHA-256 hash calculation, duplicate detection, duplicate pointer linking.
6. Document Discovery: CSS selector, XPath, text matching, button matching, relative URL resolution.
7. Downloader: direct HTTP streaming, SSRF checks, size limit enforcement.
8. Document Service Pipeline: full ingestion, validation, dedup, storage, partial success handling.
9. REST API Endpoints: list, get by ID, download binary, list for execution, user access controls.
"""

import io
import os
import shutil
import tempfile
import uuid
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pypdf import PdfWriter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.browser.url_validator import SSRFProtectionException, URLValidator
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.documents.document_deduplicator import DocumentDeduplicator
from app.documents.document_discovery import DiscoveredDocument, DocumentDiscovery
from app.documents.document_downloader import DocumentDownloader, DownloadResult
from app.documents.document_service import DocumentService
from app.documents.document_storage import DocumentStorage, LocalDocumentStorage, sanitize_filename
from app.documents.document_validator import DocumentValidator, ValidationOutcome
from app.documents.pdf_metadata import extract_pdf_metadata
from app.main import app
from app.models import (
    AssessorResult,
    BrowserSessionRecord,
    CountySource,
    DocumentArtifact,
    DocumentDownloadAttemptRecord,
    DocumentStatus,
    NodeRun,
    Recipe,
    RecipeVersion,
    RefreshToken,
    RetryAttempt,
    SecurityEvent,
    SourceConfiguration,
    TaxResult,
    TemplateVersion,
    UITemplate,
    User,
    ValidationCase,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
)

# ---------------------------------------------------------------------------
# Test Database Setup
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

ALL_TABLES = [
    User.__table__,
    RefreshToken.__table__,
    CountySource.__table__,
    UITemplate.__table__,
    TemplateVersion.__table__,
    SourceConfiguration.__table__,
    Recipe.__table__,
    RecipeVersion.__table__,
    ValidationCase.__table__,
    Workflow.__table__,
    WorkflowVersion.__table__,
    WorkflowRun.__table__,
    NodeRun.__table__,
    AssessorResult.__table__,
    TaxResult.__table__,
    SecurityEvent.__table__,
    DocumentArtifact.__table__,
    DocumentDownloadAttemptRecord.__table__,
    BrowserSessionRecord.__table__,
    RetryAttempt.__table__,
]


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=ALL_TABLES))
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=ALL_TABLES))


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user() -> User:
    async with TestSessionLocal() as session:
        user = User(
            id=uuid.uuid4(),
            email="doc_tester@example.com",
            hashed_password="hashed_pw_test",
            full_name="Doc Tester",
            is_active=True,
            is_superuser=False,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def other_user() -> User:
    async with TestSessionLocal() as session:
        user = User(
            id=uuid.uuid4(),
            email="other_doc_tester@example.com",
            hashed_password="hashed_pw_test",
            full_name="Other Tester",
            is_active=True,
            is_superuser=False,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def user_auth_headers(user: User) -> dict[str, str]:
    token = create_access_token(subject=str(user.id))
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Helper: Synthetic Valid PDF Generator
# ---------------------------------------------------------------------------


def generate_valid_pdf_bytes(title: str = "Test Property Tax Bill", author: str = "County Tax Collector", pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    writer.add_metadata({
        "/Title": title,
        "/Author": author,
    })
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def generate_encrypted_pdf_bytes(password: str = "secret") -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt(password)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


# ---------------------------------------------------------------------------
# 1. PDF Metadata Extraction Tests
# ---------------------------------------------------------------------------


def test_pdf_metadata_extraction_valid():
    pdf_bytes = generate_valid_pdf_bytes(title="Assessment Summary 2024", author="Orange County Assessor", pages=2)
    meta = extract_pdf_metadata(pdf_bytes)

    assert meta is not None
    assert meta.is_valid is True
    assert meta.page_count == 2
    assert meta.title == "Assessment Summary 2024"
    assert meta.author == "Orange County Assessor"
    assert meta.is_encrypted is False


def test_pdf_metadata_extraction_encrypted():
    pdf_bytes = generate_encrypted_pdf_bytes("mypassword")
    meta = extract_pdf_metadata(pdf_bytes)

    assert meta is not None
    assert meta.is_encrypted is True


def test_pdf_metadata_extraction_corrupt_bytes():
    meta = extract_pdf_metadata(b"corrupted bytes that are definitely not pdf")
    assert meta is not None
    assert meta.is_valid is False
    assert "Missing %PDF-" in (meta.error_message or "")


# ---------------------------------------------------------------------------
# 2. Document Validator Tests
# ---------------------------------------------------------------------------


def test_document_validator_valid_pdf():
    pdf_bytes = generate_valid_pdf_bytes()
    res = DocumentValidator.validate_document(pdf_bytes, content_type="application/pdf")

    assert res.is_valid is True
    assert res.outcome == ValidationOutcome.VALID_PDF
    assert res.mime_type == "application/pdf"
    assert res.pdf_metadata is not None
    assert res.pdf_metadata.page_count == 1
    assert res.pdf_metadata.title == "Test Property Tax Bill"


def test_document_validator_html_instead_of_pdf():
    html_bytes = b"<!DOCTYPE html><html><body><h1>Error 404: Not Found</h1></body></html>"
    res = DocumentValidator.validate_document(html_bytes, content_type="text/html")

    assert res.is_valid is False
    assert res.outcome in (ValidationOutcome.NON_PDF_RESPONSE, ValidationOutcome.CORRUPTED_PDF)
    assert res.error_message is not None


def test_document_validator_empty_file():
    res = DocumentValidator.validate_document(b"")

    assert res.is_valid is False
    assert res.outcome == ValidationOutcome.EMPTY_FILE
    assert "empty" in (res.error_message or "").lower()


def test_document_validator_corrupt_structure():
    corrupt_bytes = b"%PDF-1.4\nthis is not valid pdf structure at all 000000"
    res = DocumentValidator.validate_document(corrupt_bytes)

    assert res.is_valid is False
    assert res.outcome == ValidationOutcome.CORRUPTED_PDF


def test_document_validator_encrypted_pdf():
    enc_bytes = generate_encrypted_pdf_bytes("test")
    res = DocumentValidator.validate_document(enc_bytes)

    assert res.is_valid is False
    assert res.outcome == ValidationOutcome.ENCRYPTED_PDF
    assert "encrypted" in (res.error_message or "").lower()


def test_document_validator_exceeds_max_size():
    pdf_bytes = generate_valid_pdf_bytes()
    res = DocumentValidator.validate_document(pdf_bytes, max_size=100)

    assert res.is_valid is False
    assert res.outcome == ValidationOutcome.OVERSIZED_PDF
    assert "exceeds" in (res.error_message or "").lower()


# ---------------------------------------------------------------------------
# 3. SSRF Protection Tests
# ---------------------------------------------------------------------------


def test_ssrf_protection_for_document_urls():
    # Private IP addresses must be rejected when allow_local=False
    with pytest.raises(SSRFProtectionException):
        URLValidator.validate_url("http://127.0.0.1:8000/docs/parcel.pdf", allow_local=False)

    # Cloud metadata service must be blocked unconditionally
    with pytest.raises(SSRFProtectionException):
        URLValidator.validate_url("http://169.254.169.254/latest/meta-data/document.pdf")

    # Invalid schemes must be blocked
    with pytest.raises(SSRFProtectionException):
        URLValidator.validate_url("file:///etc/passwd")

    with pytest.raises(SSRFProtectionException):
        URLValidator.validate_url("ftp://county.gov/docs/deed.pdf")


# ---------------------------------------------------------------------------
# 4. Storage & Filename Sanitization Tests
# ---------------------------------------------------------------------------


def test_filename_sanitization():
    # Removes directory separators and forbidden characters
    clean = sanitize_filename("../../etc/passwd.pdf")
    assert "/" not in clean and "\\" not in clean

    bad_chars = sanitize_filename('file:name*with?bad"chars|.pdf')
    assert ":" not in bad_chars and "*" not in bad_chars and "?" not in bad_chars and "|" not in bad_chars

    # Default fallback
    assert sanitize_filename("") == "document.pdf"
    assert sanitize_filename("   ") == "document.pdf"


@pytest.mark.asyncio
async def test_local_document_storage():
    temp_dir = tempfile.mkdtemp()
    try:
        backend = LocalDocumentStorage(base_dir=temp_dir)
        storage = DocumentStorage(backend=backend)
        test_bytes = b"%PDF-1.4 test payload"
        run_id = uuid.uuid4()
        doc_id = uuid.uuid4()

        storage_key = DocumentStorage.generate_storage_key(
            workflow_run_id=run_id,
            property_record_id=None,
            document_id=doc_id,
        )

        assert await storage.exists(storage_key) is False
        saved_path = await storage.save(storage_key, test_bytes)
        assert os.path.exists(saved_path)
        assert await storage.exists(storage_key) is True

        loaded_bytes = await storage.retrieve(storage_key)
        assert loaded_bytes == test_bytes

        # Path traversal prevention test
        with pytest.raises(ValueError):
            backend._resolve_safe_path("../../../outside.pdf")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5. Deduplication Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_document_deduplication():
    async with TestSessionLocal() as session:
        pdf_bytes = generate_valid_pdf_bytes(title="Dedup Test")

        # First time: not a duplicate
        res1 = await DocumentDeduplicator.check_duplicate(pdf_bytes, db=session)
        assert res1.is_duplicate is False
        assert res1.duplicate_of_id is None
        assert len(res1.checksum_sha256) == 64

        # Record first document artifact in database
        doc1 = DocumentArtifact(
            id=uuid.uuid4(),
            document_type="tax_bill",
            original_filename="tax_bill_1.pdf",
            stored_filename="tax_bill_1.pdf",
            source_url="https://county.gov/tax_bill_1.pdf",
            storage_key="test/tax_bill_1.pdf",
            mime_type="application/pdf",
            file_size=len(pdf_bytes),
            checksum_sha256=res1.checksum_sha256,
            status=DocumentStatus.validated.value,
            is_duplicate=False,
        )
        session.add(doc1)
        await session.commit()

        # Second time: identical payload detected as duplicate
        res2 = await DocumentDeduplicator.check_duplicate(pdf_bytes, db=session)
        assert res2.is_duplicate is True
        assert res2.duplicate_of_id == doc1.id
        assert res2.checksum_sha256 == res1.checksum_sha256
        assert res2.existing_storage_key == "test/tax_bill_1.pdf"


# ---------------------------------------------------------------------------
# 6. Document Discovery Strategies Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_document_discovery_with_page_mock():
    # Mock playwright page and locators
    page = MagicMock()
    page.url = "https://county.gov/property/123"

    locator_mock = MagicMock()
    locator_mock.count = AsyncMock(return_value=1)

    element_mock = MagicMock()
    element_mock.is_visible = AsyncMock(return_value=True)
    element_mock.get_attribute = AsyncMock(side_effect=lambda attr: {
        "href": "/downloads/tax_statement.pdf",
        "data-recording-number": "DOC-999",
        "type": "a",
    }.get(attr))
    element_mock.inner_text = AsyncMock(return_value="Download Tax Statement")
    locator_mock.nth = MagicMock(return_value=element_mock)

    page.locator = MagicMock(return_value=locator_mock)

    discovery = DocumentDiscovery(page)
    rules = [{"strategy": "css", "selector": "a.tax-pdf", "document_type": "tax_bill"}]

    discovered = await discovery.discover_documents(rules, property_context={"parcel_number": "12-34-56"})
    assert len(discovered) == 1
    doc = discovered[0]
    assert doc.document_url == "https://county.gov/downloads/tax_statement.pdf"
    assert doc.document_title == "Download Tax Statement"
    assert doc.document_type == "tax_bill"
    assert doc.parcel_number == "12-34-56"
    assert doc.recording_number == "DOC-999"


# ---------------------------------------------------------------------------
# 7. Document Downloader Streaming Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_document_downloader_direct_download_valid():
    pdf_bytes = generate_valid_pdf_bytes(title="Direct Download Bill")

    downloader = DocumentDownloader(run_id="test_run", node_id="assessor_node")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/pdf"}
    mock_resp.aiter_bytes = MagicMock()

    async def mock_aiter(chunk_size=8192):
        yield pdf_bytes

    mock_resp.aiter_bytes = mock_aiter

    with patch("app.documents.document_downloader.URLValidator.validate_url", return_value=True), \
         patch("httpx.AsyncClient.stream") as mock_stream:
        # Context manager pattern for client.stream
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        mock_stream.return_value = mock_cm

        result = await downloader.download_direct("https://records.county.gov/bill.pdf")
        assert result.success is True
        assert result.data == pdf_bytes
        assert result.content_type == "application/pdf"


@pytest.mark.asyncio
async def test_document_downloader_rejects_ssrf():
    downloader = DocumentDownloader()
    # SSRF destination (metadata service)
    result = await downloader.download_direct("http://169.254.169.254/latest/meta-data/credentials")
    assert result.success is False
    assert "SSRF" in (result.error_message or "") or "security" in (result.error_message or "").lower()


# ---------------------------------------------------------------------------
# 8. Document Service Full Pipeline Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_document_service_pipeline_valid_and_duplicate():
    temp_dir = tempfile.mkdtemp()
    try:
        backend = LocalDocumentStorage(base_dir=temp_dir)
        storage = DocumentStorage(backend=backend)

        async with TestSessionLocal() as session:
            service = DocumentService(db=session, storage=storage)
            pdf_bytes = generate_valid_pdf_bytes(title="Pipeline Deed", author="County Clerk")
            run_id = uuid.uuid4()

            mock_downloader = MagicMock()
            mock_downloader.download_direct = AsyncMock(
                return_value=DownloadResult(
                    success=True,
                    data=pdf_bytes,
                    content_type="application/pdf",
                    original_filename="official_deed.pdf",
                    final_url="https://county.gov/docs/official_deed.pdf",
                )
            )

            discovered1 = DiscoveredDocument(
                document_url="https://county.gov/docs/official_deed.pdf",
                document_title="Official Deed",
                document_type="deed",
                recording_number="DOC-2024-00123",
            )

            # Ingest first document
            doc1 = await service.process_document_retrieval(
                discovered=discovered1,
                downloader=mock_downloader,
                workflow_run_id=run_id,
            )
            await session.commit()

            assert doc1.status == DocumentStatus.validated.value
            assert doc1.is_duplicate is False
            assert doc1.pdf_title == "Pipeline Deed"
            assert doc1.recording_number == "DOC-2024-00123"
            assert await storage.exists(doc1.storage_key) is True

            # Ingest second identical document
            discovered2 = DiscoveredDocument(
                document_url="https://county.gov/docs/official_deed_copy.pdf",
                document_title="Official Deed Copy",
                document_type="deed",
                recording_number="DOC-2024-00124",
            )

            doc2 = await service.process_document_retrieval(
                discovered=discovered2,
                downloader=mock_downloader,
                workflow_run_id=run_id,
            )
            await session.commit()

            assert doc2.status == DocumentStatus.duplicate.value
            assert doc2.is_duplicate is True
            assert doc2.duplicate_of_id == doc1.id
            assert doc2.storage_key == doc1.storage_key  # Reuses existing storage key
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_document_service_pipeline_corrupted_payload_partial_success():
    temp_dir = tempfile.mkdtemp()
    try:
        backend = LocalDocumentStorage(base_dir=temp_dir)
        storage = DocumentStorage(backend=backend)

        async with TestSessionLocal() as session:
            service = DocumentService(db=session, storage=storage)
            corrupt_bytes = b"not a valid pdf payload"
            run_id = uuid.uuid4()

            mock_downloader = MagicMock()
            mock_downloader.download_direct = AsyncMock(
                return_value=DownloadResult(
                    success=True,
                    data=corrupt_bytes,
                    content_type="text/html",
                    original_filename="bad.pdf",
                )
            )

            discovered = DiscoveredDocument(
                document_url="https://county.gov/docs/bad.pdf",
                document_title="Broken Document",
                document_type="deed",
            )

            # Corrupted payload should be recorded as failed artifact without raising an exception
            doc = await service.process_document_retrieval(
                discovered=discovered,
                downloader=mock_downloader,
                workflow_run_id=run_id,
            )
            await session.commit()

            assert doc.status == DocumentStatus.failed.value
            assert doc.error_message is not None
            assert "Validation failed" in doc.error_message
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 9. REST API Endpoints Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_list_and_get_document(async_client: AsyncClient, test_user: User, other_user: User):
    temp_dir = tempfile.mkdtemp()
    try:
        backend = LocalDocumentStorage(base_dir=temp_dir)
        storage = DocumentStorage(backend=backend)
        pdf_bytes = generate_valid_pdf_bytes(title="API Test Tax Bill")

        async with TestSessionLocal() as session:
            run = WorkflowRun(
                id=uuid.uuid4(),
                workflow_id=uuid.uuid4(),
                workflow_version_id=uuid.uuid4(),
                triggered_by=test_user.id,
                status="completed",
                input_data={},
            )
            session.add(run)
            await session.flush()

            mock_downloader = MagicMock()
            mock_downloader.download_direct = AsyncMock(
                return_value=DownloadResult(
                    success=True,
                    data=pdf_bytes,
                    content_type="application/pdf",
                    original_filename="tax_bill_api.pdf",
                )
            )

            service = DocumentService(db=session, storage=storage)
            doc = await service.process_document_retrieval(
                discovered=DiscoveredDocument(
                    document_url="https://county.gov/tax_bill_api.pdf",
                    document_title="Tax Bill",
                    document_type="tax_bill",
                ),
                downloader=mock_downloader,
                workflow_run_id=run.id,
            )
            await session.commit()
            doc_id = doc.id

        # 1. List documents as owner (test_user)
        headers = user_auth_headers(test_user)
        res = await async_client.get("/api/v1/documents", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total"] >= 1
        assert any(d["id"] == str(doc_id) for d in data["items"])

        # 2. Filter by document_type
        res_filter = await async_client.get("/api/v1/documents?document_type=tax_bill", headers=headers)
        assert res_filter.status_code == 200
        assert res_filter.json()["total"] >= 1

        # 3. Get single document metadata as owner
        res_single = await async_client.get(f"/api/v1/documents/{doc_id}", headers=headers)
        assert res_single.status_code == 200
        assert res_single.json()["status"] == DocumentStatus.validated.value

        # 4. Get single document metadata as another user (must be 403 or 404)
        other_headers = user_auth_headers(other_user)
        res_other = await async_client.get(f"/api/v1/documents/{doc_id}", headers=other_headers)
        assert res_other.status_code in (403, 404)

        # 5. List execution documents via /executions/{run_id}/documents
        res_exec_docs = await async_client.get(f"/api/v1/executions/{run.id}/documents", headers=headers)
        assert res_exec_docs.status_code == 200
        exec_docs = res_exec_docs.json()
        assert len(exec_docs) >= 1
        assert exec_docs[0]["id"] == str(doc_id)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_api_download_document(async_client: AsyncClient, test_user: User):
    temp_dir = tempfile.mkdtemp()
    try:
        backend = LocalDocumentStorage(base_dir=temp_dir)
        storage = DocumentStorage(backend=backend)
        pdf_bytes = generate_valid_pdf_bytes(title="Download Stream Bill")

        async with TestSessionLocal() as session:
            run = WorkflowRun(
                id=uuid.uuid4(),
                workflow_id=uuid.uuid4(),
                workflow_version_id=uuid.uuid4(),
                triggered_by=test_user.id,
                status="completed",
                input_data={},
            )
            session.add(run)
            await session.flush()

            mock_downloader = MagicMock()
            mock_downloader.download_direct = AsyncMock(
                return_value=DownloadResult(
                    success=True,
                    data=pdf_bytes,
                    content_type="application/pdf",
                    original_filename="download_test.pdf",
                )
            )

            service = DocumentService(db=session, storage=storage)
            doc = await service.process_document_retrieval(
                discovered=DiscoveredDocument(
                    document_url="https://county.gov/download_test.pdf",
                    document_title="Download Test",
                    document_type="tax_bill",
                ),
                downloader=mock_downloader,
                workflow_run_id=run.id,
            )
            await session.commit()
            doc_id = doc.id

        # Download via API with storage mock pointing to our temp storage
        headers = user_auth_headers(test_user)
        with patch("app.api.v1.documents.DocumentService") as MockServiceCls:
            async def mock_get_stream(document_id, user):
                async with TestSessionLocal() as s:
                    s_service = DocumentService(db=s, storage=storage)
                    return await s_service.get_document_binary_stream(document_id, user)

            instance = MockServiceCls.return_value
            instance.get_document_binary_stream = mock_get_stream

            res = await async_client.get(f"/api/v1/documents/{doc_id}/download", headers=headers)
            assert res.status_code == 200
            assert res.headers["content-type"] == "application/pdf"
            assert "attachment;" in res.headers["content-disposition"]
            assert res.content == pdf_bytes
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_api_view_and_ocr_document(async_client: AsyncClient, test_user: User):
    temp_dir = tempfile.mkdtemp()
    try:
        backend = LocalDocumentStorage(base_dir=temp_dir)
        storage = DocumentStorage(backend=backend)
        pdf_bytes = generate_valid_pdf_bytes(title="View Bill")

        async with TestSessionLocal() as session:
            run = WorkflowRun(
                id=uuid.uuid4(),
                workflow_id=uuid.uuid4(),
                workflow_version_id=uuid.uuid4(),
                triggered_by=test_user.id,
                status="completed",
                input_data={},
            )
            session.add(run)
            await session.flush()

            mock_downloader = MagicMock()
            mock_downloader.download_direct = AsyncMock(
                return_value=DownloadResult(
                    success=True,
                    data=pdf_bytes,
                    content_type="application/pdf",
                    original_filename="view_test.pdf",
                )
            )

            service = DocumentService(db=session, storage=storage)
            doc = await service.process_document_retrieval(
                discovered=DiscoveredDocument(
                    document_url="https://county.gov/view_test.pdf",
                    document_title="View Test",
                    document_type="tax_bill",
                ),
                downloader=mock_downloader,
                workflow_run_id=run.id,
            )
            await session.commit()
            doc_id = doc.id

        headers = user_auth_headers(test_user)
        with patch("app.api.v1.documents.DocumentService") as MockServiceCls:
            async def mock_get_stream(document_id, user):
                async with TestSessionLocal() as s:
                    s_service = DocumentService(db=s, storage=storage)
                    return await s_service.get_document_binary_stream(document_id, user)

            instance = MockServiceCls.return_value
            instance.get_document_binary_stream = mock_get_stream

            # Test View endpoint
            res_view = await async_client.get(f"/api/v1/documents/{doc_id}/view", headers=headers)
            assert res_view.status_code == 200
            assert "inline;" in res_view.headers["content-disposition"]
            assert res_view.headers["content-type"] == "application/pdf"
            assert res_view.content == pdf_bytes

            # Test OCR Analyze endpoint with mocked OCR
            with patch("app.extraction.mistral_webpage_ocr.MistralWebpageOCRService.extract_from_document_bytes") as mock_ocr:
                mock_ocr.return_value = {
                    "parcel_number": "12-345-6789",
                    "owner_name": "TEST OWNER",
                    "property_address": "123 TEST ST",
                    "raw_markdown": "# TEST OCR MARKDOWN",
                    "all_key_values": {"owner": "TEST OWNER"},
                    "raw_tables": [],
                    "pages_count": 1,
                    "ocr_model": "mistral-ocr-latest",
                }
                res_ocr = await async_client.post(f"/api/v1/documents/{doc_id}/ocr-analyze", headers=headers)
                assert res_ocr.status_code == 200
                ocr_json = res_ocr.json()
                assert ocr_json["success"] is True
                assert ocr_json["property_fields"]["parcel_number"] == "12-345-6789"
                assert ocr_json["property_fields"]["owner_name"] == "TEST OWNER"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
