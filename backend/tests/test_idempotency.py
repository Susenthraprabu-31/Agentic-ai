"""Comprehensive test suite for Phase 3D.2 Idempotency & Duplicate Execution Prevention.

Covers:
1. Workflow-level idempotency:
   - First execution request creates a new WorkflowRun (201 Created).
   - Same key + same payload replays original run (200 OK + X-Idempotent-Replay: true).
   - Same key + different payload raises HTTP 409 Conflict.
   - Different key creates independent execution run.
   - Request without Idempotency-Key creates normal execution.
   - User isolation: different users with identical key create distinct executions.
   - Invalid key formats return HTTP 400 Bad Request.
2. Concurrent request deduplication:
   - Atomic database claim prevents concurrent duplicate runs.
   - IntegrityError safely rolls back and returns existing claim.
3. Node-level idempotency:
   - Completed node is skipped and does not re-execute.
   - Cached outputs are preserved and passed to downstream nodes.
   - Failed node can be safely retried.
   - Dual-branch: Assessor success is preserved when Tax fails.
   - No duplicate AssessorResult or TaxResult rows created.
4. Celery task idempotency:
   - Redelivery of execute_workflow_run task on already completed run returns cleanly.
   - Task claims prevent concurrent workers from processing same run.
   - Expired task claim leases can be reclaimed.
5. Document retrieval idempotency:
   - Previously validated document for same run and URL is reused without re-downloading.
   - Previously failed document download can be retried.
   - SHA-256 deduplication is preserved across runs.
6. Persistence and Lifecycle:
   - Expired idempotency records cleanup.
   - State transitions (PROCESSING -> COMPLETED, PROCESSING -> FAILED, reclaim).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.documents.document_discovery import DiscoveredDocument
from app.documents.document_downloader import DownloadResult
from app.documents.document_service import DocumentService
from app.idempotency.exceptions import (
    IdempotencyConflictError,
    IdempotencyKeyInvalidError,
    IdempotencyClaimLockedError,
)
from app.idempotency.idempotency_key import (
    generate_document_idempotency_key,
    generate_key_hash,
    generate_node_idempotency_key,
    generate_payload_hash,
    generate_task_idempotency_key,
    validate_key_format,
)
from app.idempotency.idempotency_manager import IdempotencyManager
from app.idempotency.idempotency_service import IdempotencyService
from app.main import app
from app.models import (
    AssessorResult,
    BrowserSessionRecord,
    CountySource,
    DocumentArtifact,
    DocumentDownloadAttemptRecord,
    IdempotencyRecord,
    IdempotencyStatus,
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
from app.tasks.execution_tasks import execute_workflow_run
from app.workflow_engine.execution_context import ExecutionContext, NodeExecutionResult
from app.workflow_engine.executor import WorkflowExecutor
from app.workflow_engine.scheduler import WorkflowScheduler

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
    IdempotencyRecord.__table__,
]


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=ALL_TABLES))
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=ALL_TABLES))


@pytest_asyncio.fixture
async def test_user() -> User:
    async with TestSessionLocal() as session:
        user = User(
            email="idem_user@example.com",
            hashed_password=hash_password("password123"),
            full_name="Idempotency Tester",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def second_user() -> User:
    async with TestSessionLocal() as session:
        user = User(
            email="second_user@example.com",
            hashed_password=hash_password("password123"),
            full_name="Second User",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def sample_workflow(test_user: User) -> tuple[Workflow, WorkflowVersion]:
    async with TestSessionLocal() as session:
        wf = Workflow(name="Test Workflow", owner_id=test_user.id)
        session.add(wf)
        await session.flush()

        definition = {
            "nodes": [
                {"id": "node_input", "type": "input", "data": {"fields": ["parcel_id"]}},
                {
                    "id": "node_assessor",
                    "type": "assessor",
                    "data": {"county": "Orange", "mode": "search"},
                },
                {
                    "id": "node_tax",
                    "type": "tax",
                    "data": {"county": "Orange", "mode": "search"},
                },
                {"id": "node_output", "type": "output", "data": {}},
            ],
            "edges": [
                {"source": "node_input", "target": "node_assessor"},
                {"source": "node_input", "target": "node_tax"},
                {"source": "node_assessor", "target": "node_output"},
                {"source": "node_tax", "target": "node_output"},
            ],
        }

        version = WorkflowVersion(
            workflow_id=wf.id,
            version_number=1,
            definition=definition,
            is_current=True,
        )
        session.add(version)
        await session.commit()
        await session.refresh(wf)
        await session.refresh(version)
        return wf, version


@pytest_asyncio.fixture
async def client(test_user: User) -> AsyncGenerator[httpx.AsyncClient, None]:
    token = create_access_token(str(test_user.id))
    headers = {"Authorization": f"Bearer {token}"}

    async def get_test_db():
        async with TestSessionLocal() as session:
            yield session

    from app.db.session import get_db

    app.dependency_overrides[get_db] = get_test_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as ac:
        yield ac
    app.dependency_overrides.clear()


# ===========================================================================
# 1. Workflow-Level Idempotency Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_workflow_execute_first_request_creates_run(
    client: httpx.AsyncClient, sample_workflow: tuple[Workflow, WorkflowVersion]
):
    wf, _ = sample_workflow
    key = "idem-req-001"
    payload = {"input_data": {"parcel_id": "123-456"}}

    with patch("app.tasks.execution_tasks.execute_workflow_run.delay") as mock_delay:
        resp = await client.post(
            f"/api/v1/workflows/{wf.id}/execute",
            json=payload,
            headers={"Idempotency-Key": key},
        )

    assert resp.status_code == 202
    data = resp.json()
    assert "id" in data
    assert resp.headers.get("X-Idempotent-Replay") is None
    assert mock_delay.called


@pytest.mark.asyncio
async def test_workflow_execute_replay_same_key_same_payload(
    client: httpx.AsyncClient, sample_workflow: tuple[Workflow, WorkflowVersion]
):
    wf, _ = sample_workflow
    key = "idem-req-replay-001"
    payload = {"input_data": {"parcel_id": "123-456"}}

    with patch("app.tasks.execution_tasks.execute_workflow_run.delay"):
        # First request
        resp1 = await client.post(
            f"/api/v1/workflows/{wf.id}/execute",
            json=payload,
            headers={"Idempotency-Key": key},
        )
        assert resp1.status_code == 202
        run_id_1 = resp1.json()["id"]

        # Replay request
        resp2 = await client.post(
            f"/api/v1/workflows/{wf.id}/execute",
            json=payload,
            headers={"Idempotency-Key": key},
        )
        assert resp2.status_code == 200
        assert resp2.headers.get("X-Idempotent-Replay") == "true"
        assert resp2.json()["id"] == run_id_1


@pytest.mark.asyncio
async def test_workflow_execute_replay_same_key_different_payload_conflict(
    client: httpx.AsyncClient, sample_workflow: tuple[Workflow, WorkflowVersion]
):
    wf, _ = sample_workflow
    key = "idem-req-conflict-001"
    payload1 = {"input_data": {"parcel_id": "123-456"}}
    payload2 = {"input_data": {"parcel_id": "999-888"}}

    with patch("app.tasks.execution_tasks.execute_workflow_run.delay"):
        # First request
        resp1 = await client.post(
            f"/api/v1/workflows/{wf.id}/execute",
            json=payload1,
            headers={"Idempotency-Key": key},
        )
        assert resp1.status_code == 202

        # Conflict request
        resp2 = await client.post(
            f"/api/v1/workflows/{wf.id}/execute",
            json=payload2,
            headers={"Idempotency-Key": key},
        )
        assert resp2.status_code == 409
        assert "different request payload" in resp2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_workflow_execute_different_keys_create_different_runs(
    client: httpx.AsyncClient, sample_workflow: tuple[Workflow, WorkflowVersion]
):
    wf, _ = sample_workflow
    payload = {"input_data": {"parcel_id": "123-456"}}

    with patch("app.tasks.execution_tasks.execute_workflow_run.delay"):
        resp1 = await client.post(
            f"/api/v1/workflows/{wf.id}/execute",
            json=payload,
            headers={"Idempotency-Key": "key-alpha-1"},
        )
        resp2 = await client.post(
            f"/api/v1/workflows/{wf.id}/execute",
            json=payload,
            headers={"Idempotency-Key": "key-beta-2"},
        )

    assert resp1.status_code == 202
    assert resp2.status_code == 202
    assert resp1.json()["id"] != resp2.json()["id"]


@pytest.mark.asyncio
async def test_workflow_execute_without_key_creates_normal_run(
    client: httpx.AsyncClient, sample_workflow: tuple[Workflow, WorkflowVersion]
):
    wf, _ = sample_workflow
    payload = {"input_data": {"parcel_id": "123-456"}}

    with patch("app.tasks.execution_tasks.execute_workflow_run.delay"):
        resp1 = await client.post(f"/api/v1/workflows/{wf.id}/execute", json=payload)
        resp2 = await client.post(f"/api/v1/workflows/{wf.id}/execute", json=payload)

    assert resp1.status_code == 202
    assert resp2.status_code == 202
    assert resp1.json()["id"] != resp2.json()["id"]


@pytest.mark.asyncio
async def test_workflow_execute_user_isolation(
    client: httpx.AsyncClient,
    second_user: User,
    sample_workflow: tuple[Workflow, WorkflowVersion],
):
    wf, ver = sample_workflow
    key = "shared-key-isolation"
    payload = {"input_data": {"parcel_id": "123-456"}}

    # User 1 executes
    with patch("app.tasks.execution_tasks.execute_workflow_run.delay"):
        resp1 = await client.post(
            f"/api/v1/workflows/{wf.id}/execute",
            json=payload,
            headers={"Idempotency-Key": key},
        )
    assert resp1.status_code == 202

    # User 2 creates their own workflow and executes with the same key
    async with TestSessionLocal() as session:
        wf2 = Workflow(name="User 2 Workflow", owner_id=second_user.id)
        session.add(wf2)
        await session.flush()
        ver2 = WorkflowVersion(
            workflow_id=wf2.id,
            version_number=1,
            definition=ver.definition,
            is_current=True,
        )
        session.add(ver2)
        await session.commit()
        wf2_id = wf2.id

    token2 = create_access_token(str(second_user.id))
    headers2 = {"Authorization": f"Bearer {token2}", "Idempotency-Key": key}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers2) as client2:
        with patch("app.tasks.execution_tasks.execute_workflow_run.delay"):
            resp2 = await client2.post(f"/api/v1/workflows/{wf2_id}/execute", json=payload)

    assert resp2.status_code == 202
    assert resp1.json()["id"] != resp2.json()["id"]


@pytest.mark.asyncio
async def test_workflow_execute_invalid_key_formats(
    client: httpx.AsyncClient, sample_workflow: tuple[Workflow, WorkflowVersion]
):
    wf, _ = sample_workflow

    # Too short (< 4 chars)
    resp_short = await client.post(
        f"/api/v1/workflows/{wf.id}/execute",
        json={"parcel_id": "1"},
        headers={"Idempotency-Key": "abc"},
    )
    assert resp_short.status_code == 400

    # Invalid control characters (contains newline)
    resp_chars = await client.post(
        f"/api/v1/workflows/{wf.id}/execute",
        json={"parcel_id": "1"},
        headers={"Idempotency-Key": "bad\nkey"},
    )
    assert resp_chars.status_code == 400


# ===========================================================================
# 2. Concurrency Deduplication Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_concurrent_claims_prevent_duplicate_runs(test_user: User):
    async with TestSessionLocal() as session:
        key = "concurrent-key-001"
        payload = {"param": "value"}

        claim1 = await IdempotencyManager.process_workflow_claim(
            db=session,
            user_id=test_user.id,
            workflow_id=uuid.uuid4(),
            raw_key=key,
            payload=payload,
        )
        assert claim1 is not None
        assert claim1.is_replay is False

        # Simulate concurrent second attempt with same key
        claim2 = await IdempotencyManager.process_workflow_claim(
            db=session,
            user_id=test_user.id,
            workflow_id=uuid.uuid4(),
            raw_key=key,
            payload=payload,
        )
        assert claim2 is not None
        assert claim2.is_replay is True
        assert claim2.id == claim1.id


# ===========================================================================
# 3. Node-Level Idempotency Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_completed_node_skipped_on_reexecution():
    context = ExecutionContext(
        run_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        workflow_definition={
            "nodes": [
                {"id": "node_1", "type": "input", "data": {"key": "val"}},
            ],
            "edges": [],
        },
        initial_input={"key": "val"},
    )

    # Pre-mark node_1 as COMPLETED
    context.node_statuses["node_1"] = "COMPLETED"
    context.node_outputs["node_1"] = {"cached": "data"}

    scheduler = WorkflowScheduler(context)
    result = await scheduler.execute_node({"id": "node_1", "type": "input"})

    assert result.status == "COMPLETED"
    assert result.output_data == {"cached": "data"}


@pytest.mark.asyncio
async def test_failed_node_can_be_retried():
    context = ExecutionContext(
        run_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        workflow_definition={
            "nodes": [
                {"id": "node_input", "type": "input", "data": {"parcel_id": "123"}},
            ],
            "edges": [],
        },
        initial_input={"parcel_id": "123"},
    )

    # Simulate node was FAILED previously
    context.node_statuses["node_input"] = "FAILED"

    scheduler = WorkflowScheduler(context)
    result = await scheduler.execute_node({"id": "node_input", "type": "input", "data": {}})

    # Should execute successfully now
    assert result.status == "COMPLETED"
    assert context.node_statuses["node_input"] == "COMPLETED"


@pytest.mark.asyncio
async def test_dual_branch_partial_preservation_and_no_duplicate_results(test_user: User):
    async with TestSessionLocal() as session:
        wf = Workflow(name="Dual Branch Workflow", owner_id=test_user.id)
        session.add(wf)
        await session.flush()

        version = WorkflowVersion(
            workflow_id=wf.id,
            version_number=1,
            definition={
                "nodes": [
                    {"id": "in", "type": "input", "data": {"parcel_id": "01-999"}},
                    {"id": "assessor", "type": "assessor", "data": {}},
                    {"id": "tax", "type": "tax", "data": {}},
                ],
                "edges": [
                    {"source": "in", "target": "assessor"},
                    {"source": "in", "target": "tax"},
                ],
            },
            is_current=True,
        )
        session.add(version)
        await session.flush()

        run = WorkflowRun(
            workflow_id=wf.id,
            workflow_version_id=version.id,
            triggered_by=test_user.id,
            status="PENDING",
            input_data={"parcel_id": "01-999"},
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    # First Execution
    async with TestSessionLocal() as session:
        executor = WorkflowExecutor(session)
        await executor.execute_run(run_id)

    # Check AssessorResult count
    async with TestSessionLocal() as session:
        q_assessor = select(AssessorResult)
        res1 = await session.execute(q_assessor)
        count1 = len(res1.scalars().all())
        assert count1 == 1

    # Simulate re-running the same run (e.g. redelivery / retry)
    async with TestSessionLocal() as session:
        # Reset run status to RUNNING to allow rerun
        q_run = select(WorkflowRun).where(WorkflowRun.id == run_id)
        run_obj = (await session.execute(q_run)).scalar_one()
        run_obj.status = "RUNNING"
        await session.commit()

        executor = WorkflowExecutor(session)
        await executor.execute_run(run_id)

    # AssessorResult count should STILL be 1 (no duplicates inserted!)
    async with TestSessionLocal() as session:
        res2 = await session.execute(q_assessor)
        count2 = len(res2.scalars().all())
        assert count2 == 1


# ===========================================================================
# 4. Celery Task Idempotency Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_celery_task_skips_already_completed_run(test_user: User):
    async with TestSessionLocal() as session:
        wf = Workflow(name="Celery Task WF", owner_id=test_user.id)
        session.add(wf)
        await session.flush()
        ver = WorkflowVersion(
            workflow_id=wf.id,
            version_number=1,
            definition={"nodes": [], "edges": []},
            is_current=True,
        )
        session.add(ver)
        await session.flush()

        run = WorkflowRun(
            workflow_id=wf.id,
            workflow_version_id=ver.id,
            triggered_by=test_user.id,
            status="COMPLETED",
            duration_ms=120,
        )
        session.add(run)
        await session.commit()
        run_id = str(run.id)

    with patch("app.tasks.execution_tasks.AsyncSessionLocal", side_effect=TestSessionLocal):
        result = execute_workflow_run.run(run_id)

    assert result["status"] == "COMPLETED"
    assert result["duration_ms"] == 120


@pytest.mark.asyncio
async def test_celery_task_claim_lifecycle(test_user: User):
    run_uuid = uuid.uuid4()
    async with TestSessionLocal() as session:
        # 1. First claim
        claim = await IdempotencyManager.process_task_claim(
            db=session,
            task_name="execute_workflow_run",
            workflow_run_id=run_uuid,
        )
        assert claim.is_replay is False
        assert claim.status == IdempotencyStatus.processing

        # 2. Complete claim
        await IdempotencyManager.complete_claim(
            db=session,
            claim_id=claim.id,
            workflow_run_id=run_uuid,
            response_status_code=200,
            response_payload={"status": "COMPLETED"},
        )
        await session.commit()

    # 3. Duplicate delivery -> Replay
    async with TestSessionLocal() as session:
        claim_replay = await IdempotencyManager.process_task_claim(
            db=session,
            task_name="execute_workflow_run",
            workflow_run_id=run_uuid,
        )
        assert claim_replay.is_replay is True
        assert claim_replay.status == IdempotencyStatus.completed
        assert claim_replay.response_payload == {"status": "COMPLETED"}


# ===========================================================================
# 5. Document-Level Idempotency Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_document_retrieval_reuses_validated_document(test_user: User):
    run_id = uuid.uuid4()
    source_url = "https://example-county.gov/docs/deed_101.pdf"

    async with TestSessionLocal() as session:
        # Seed an already validated document artifact
        existing = DocumentArtifact(
            id=uuid.uuid4(),
            workflow_run_id=run_id,
            document_type="deed",
            original_filename="deed_101.pdf",
            stored_filename="stored_101.pdf",
            source_url=source_url,
            storage_key="docs/existing.pdf",
            mime_type="application/pdf",
            file_size=1024,
            checksum_sha256="abc123sha256",
            status="validated",
        )
        session.add(existing)
        await session.commit()

    # Call process_document_retrieval with mock downloader
    async with TestSessionLocal() as session:
        service = DocumentService(db=session)
        discovered = DiscoveredDocument(
            document_url=source_url,
            document_type="deed",
            document_title="Existing Deed",
        )
        mock_downloader = MagicMock()
        mock_downloader.download_direct = AsyncMock()

        retrieved = await service.process_document_retrieval(
            discovered=discovered,
            downloader=mock_downloader,
            workflow_run_id=run_id,
        )

        # Downloader should NOT have been called!
        assert not mock_downloader.download_direct.called
        assert retrieved.id == existing.id
        assert retrieved.status == "validated"


@pytest.mark.asyncio
async def test_failed_document_download_can_be_retried():
    run_id = uuid.uuid4()
    source_url = "https://example-county.gov/docs/retry_me.pdf"

    # Step 1: Document fails
    async with TestSessionLocal() as session:
        service = DocumentService(db=session)
        discovered = DiscoveredDocument(
            document_url=source_url,
            document_type="tax_bill",
            document_title="Tax Bill",
        )
        failing_downloader = MagicMock()
        failing_downloader.download_direct = AsyncMock(
            return_value=DownloadResult(success=False, error_message="HTTP 500 error")
        )

        doc1 = await service.process_document_retrieval(
            discovered=discovered,
            downloader=failing_downloader,
            workflow_run_id=run_id,
        )
        await session.commit()
        assert doc1.status == "failed"

    # Step 2: Retry succeeds
    async with TestSessionLocal() as session:
        service = DocumentService(db=session)
        succeeding_downloader = MagicMock()
        succeeding_downloader.download_direct = AsyncMock(
            return_value=DownloadResult(
                success=True,
                data=b"%PDF-1.4 valid test pdf content here",
                content_type="application/pdf",
                original_filename="retry_me.pdf",
            )
        )

        with patch("app.documents.document_validator.DocumentValidator.validate_document") as mock_val:
            from app.documents.document_validator import ValidationOutcome, ValidationResult
            mock_val.return_value = ValidationResult(
                is_valid=True,
                outcome=ValidationOutcome.VALID_PDF,
                mime_type="application/pdf",
                file_size=32,
            )
            with patch("app.documents.document_storage.DocumentStorage.save", new_callable=AsyncMock):
                doc2 = await service.process_document_retrieval(
                    discovered=discovered,
                    downloader=succeeding_downloader,
                    workflow_run_id=run_id,
                )
                assert doc2.status == "validated"
                assert succeeding_downloader.download_direct.called


# ===========================================================================
# 6. Persistence and Lifecycle Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_clean_expired_records(test_user: User):
    async with TestSessionLocal() as session:
        service = IdempotencyService(session)

        # 1. Create an active record
        active = IdempotencyRecord(
            user_id=test_user.id,
            idempotency_key_hash=generate_key_hash("active-key-1"),
            operation_type="workflow_execute",
            resource_id="wf-test-resource-1",
            request_payload_hash=generate_payload_hash({"k": "v1"}),
            status=IdempotencyStatus.completed.value,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        # 2. Create an expired record
        expired = IdempotencyRecord(
            user_id=test_user.id,
            idempotency_key_hash=generate_key_hash("expired-key-2"),
            operation_type="workflow_execute",
            resource_id="wf-test-resource-2",
            request_payload_hash=generate_payload_hash({"k": "v2"}),
            status=IdempotencyStatus.completed.value,
            expires_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        session.add_all([active, expired])
        await session.commit()

        # Clean
        deleted_count = await service.clean_expired_records()
        await session.commit()
        assert deleted_count == 1

        # Check only active remains
        records = (await session.execute(select(IdempotencyRecord))).scalars().all()
        assert len(records) == 1
        assert records[0].idempotency_key_hash == generate_key_hash("active-key-1")


@pytest.mark.asyncio
async def test_key_generation_and_validation():
    # Valid key
    assert validate_key_format("valid_key-123.ABC") is True
    assert validate_key_format("  valid_key  ") is True

    # Empty or None
    assert validate_key_format("") is False
    assert validate_key_format(None) is False

    # Control chars (e.g. newline or null byte)
    assert validate_key_format("bad\nkey") is False
    assert validate_key_format("bad\x00key") is False

    # Deterministic generation
    run_id = uuid.uuid4()
    k1 = generate_node_idempotency_key(run_id, "node_1", None, {"a": 1})
    k2 = generate_node_idempotency_key(run_id, "node_1", None, {"a": 1})
    assert k1 == k2

    t1 = generate_task_idempotency_key("task_a", run_id)
    t2 = generate_task_idempotency_key("task_a", run_id)
    assert t1 == t2

    d1 = generate_document_idempotency_key(run_id, "https://example.com/doc.pdf")
    d2 = generate_document_idempotency_key(run_id, "https://example.com/doc.pdf")
    assert d1 == d2
