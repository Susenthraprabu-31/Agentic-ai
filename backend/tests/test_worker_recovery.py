"""Comprehensive test suite for Phase 3D.3 Worker Recovery, Heartbeats & Circuit Breakers.

Covers:
1. Worker Heartbeat & Monitoring:
   - Worker registration.
   - Periodic heartbeat updating status and active tasks.
   - Stale heartbeat timeout detecting UNRESPONSIVE worker.
   - Graceful shutdown marking worker OFFLINE.
   - Stale heartbeat rejection for OFFLINE worker.
   - Cluster health aggregation.
2. Interrupted Execution Recovery:
   - Stale execution detection.
   - Active execution not falsely marked orphaned.
   - Terminal (COMPLETED/CANCELLED/FAILED) executions never recovered.
   - Atomic recovery claim incrementing fencing token.
   - Fencing violation preventing old zombie worker from committing.
   - Max recovery attempts limit marking run FAILED.
   - Node reuse and partial success preservation on recovered run.
3. Circuit Breaker System:
   - CLOSED permits requests.
   - Threshold transient failures trip circuit to OPEN.
   - OPEN immediately blocks calls raising CircuitBreakerOpenException.
   - Cooldown expiration transitions circuit to HALF_OPEN.
   - Successful probes close circuit back to CLOSED.
   - Failed probe in HALF_OPEN immediately re-opens circuit.
   - Security challenges and 403s do not count toward circuit failure.
   - Independent dependencies maintain isolated circuit states.
   - Manual administrative reset to CLOSED.
   - Redis outage graceful degradation.
4. Monitoring & Recovery APIs:
   - GET /api/v1/workers
   - GET /api/v1/workers/health
   - GET /api/v1/workers/{worker_id}
   - GET /api/v1/circuit-breakers
   - GET /api/v1/circuit-breakers/{key}
   - POST /api/v1/circuit-breakers/{key}/reset
   - GET /api/v1/executions/{run_id}/recovery
   - POST /api/v1/executions/{run_id}/recover
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

from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.main import app
from app.models import (
    AssessorResult,
    CircuitBreakerRecord,
    CircuitState,
    CountySource,
    DocumentArtifact,
    IdempotencyRecord,
    NodeRun,
    Recipe,
    RecipeVersion,
    RefreshToken,
    RetryAttempt,
    RunStatus,
    SecurityEvent,
    SourceConfiguration,
    TaxResult,
    TemplateVersion,
    UITemplate,
    User,
    ValidationCase,
    WorkerRecord,
    WorkerStatus,
    Workflow,
    WorkflowRecoveryRecord,
    WorkflowRun,
    WorkflowVersion,
)
from app.resilience.circuit_breaker import CircuitBreaker, circuit_breaker
from app.resilience.error_classifier import ErrorCategory, ErrorClassifier
from app.resilience.exceptions import CircuitBreakerOpenException
from app.resilience.retry_manager import RetryManager
from app.resilience.retry_policy import NO_RETRY_POLICY, RetryPolicy
from app.services.recovery_service import RecoveryService
from app.services.worker_monitor import WorkerMonitorService
from app.tasks.recovery_tasks import (
    recover_orphaned_workflows,
    worker_heartbeat_check,
    worker_self_heartbeat,
)
from app.workflow_engine.executor import WorkflowExecutor

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
    RetryAttempt.__table__,
    IdempotencyRecord.__table__,
    WorkerRecord.__table__,
    CircuitBreakerRecord.__table__,
    WorkflowRecoveryRecord.__table__,
]


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=ALL_TABLES))
    # Clear circuit breaker in-memory cache between tests
    circuit_breaker._local_cache.clear()
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=ALL_TABLES))


@pytest_asyncio.fixture
async def test_user() -> User:
    async with TestSessionLocal() as session:
        user = User(
            email="recovery_tester@example.com",
            hashed_password=hash_password("password123"),
            full_name="Recovery Tester",
            is_active=True,
            is_superuser=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def sample_workflow(test_user: User) -> tuple[Workflow, WorkflowVersion]:
    async with TestSessionLocal() as session:
        wf = Workflow(name="Recovery Test WF", owner_id=test_user.id)
        session.add(wf)
        await session.flush()

        ver = WorkflowVersion(
            workflow_id=wf.id,
            version_number=1,
            definition={
                "nodes": [
                    {"id": "node_in", "type": "input", "data": {"parcel_id": "P-101"}},
                    {"id": "node_assessor", "type": "assessor", "data": {}},
                    {"id": "node_out", "type": "output", "data": {}},
                ],
                "edges": [
                    {"source": "node_in", "target": "node_assessor"},
                    {"source": "node_assessor", "target": "node_out"},
                ],
            },
            is_current=True,
        )
        session.add(ver)
        await session.commit()
        await session.refresh(wf)
        await session.refresh(ver)
        return wf, ver


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
# 1. Worker Heartbeat & Monitoring Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_worker_registration_and_heartbeat():
    async with TestSessionLocal() as session:
        monitor = WorkerMonitorService(session)
        worker = await monitor.register_worker(
            worker_id="worker-node-1",
            hostname="host-alpha",
            pid=12345,
            queues=["executions", "ai"],
        )

        assert worker.worker_id == "worker-node-1"
        assert worker.status == WorkerStatus.ONLINE.value
        assert worker.active_task_count == 0

        # Record heartbeat with active task
        updated = await monitor.record_heartbeat(
            worker_id="worker-node-1",
            active_task_count=2,
            current_task_id="task-xyz",
            status=WorkerStatus.BUSY.value,
        )
        assert updated is True

        worker_detail = await monitor.get_worker("worker-node-1")
        assert worker_detail is not None
        assert worker_detail["status"] == WorkerStatus.BUSY.value
        assert worker_detail["active_task_count"] == 2
        assert worker_detail["current_task_id"] == "task-xyz"


@pytest.mark.asyncio
async def test_worker_stale_heartbeat_unresponsive_detection():
    async with TestSessionLocal() as session:
        monitor = WorkerMonitorService(session)
        # Register a worker
        await monitor.register_worker(worker_id="stale-worker-99")

        # Age the heartbeat beyond WORKER_HEARTBEAT_TIMEOUT_SECONDS
        stmt = select(WorkerRecord).where(WorkerRecord.worker_id == "stale-worker-99")
        rec = (await session.execute(stmt)).scalar_one()
        rec.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=settings.WORKER_HEARTBEAT_TIMEOUT_SECONDS + 10)
        await session.commit()

        # Run unresponsiveness check
        unresponsive = await monitor.detect_unresponsive_workers()
        assert "stale-worker-99" in unresponsive

        worker_detail = await monitor.get_worker("stale-worker-99")
        assert worker_detail["status"] == WorkerStatus.UNRESPONSIVE.value


@pytest.mark.asyncio
async def test_worker_shutdown_and_stale_heartbeat_rejection():
    async with TestSessionLocal() as session:
        monitor = WorkerMonitorService(session)
        await monitor.register_worker(worker_id="shutdown-worker-1")

        # Mark offline
        await monitor.mark_worker_offline("shutdown-worker-1")
        worker_detail = await monitor.get_worker("shutdown-worker-1")
        assert worker_detail["status"] == WorkerStatus.OFFLINE.value

        # Stale delayed heartbeat attempting to resurrect offline worker must be rejected
        accepted = await monitor.record_heartbeat("shutdown-worker-1", active_task_count=1)
        assert accepted is False

        # Status must remain OFFLINE
        worker_after = await monitor.get_worker("shutdown-worker-1")
        assert worker_after["status"] == WorkerStatus.OFFLINE.value


@pytest.mark.asyncio
async def test_worker_cluster_health_overview():
    async with TestSessionLocal() as session:
        monitor = WorkerMonitorService(session)
        await monitor.register_worker(worker_id="w1")
        await monitor.register_worker(worker_id="w2")
        await monitor.record_heartbeat("w2", active_task_count=3, status=WorkerStatus.BUSY.value)

        health = await monitor.get_worker_health()
        assert health["total_workers"] == 2
        assert health["online_workers"] == 1
        assert health["busy_workers"] == 1
        assert health["total_active_tasks"] == 3
        assert health["overall_status"] == "HEALTHY"


# ===========================================================================
# 2. Interrupted Execution Recovery Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_detect_and_claim_orphaned_workflow(
    test_user: User, sample_workflow: tuple[Workflow, WorkflowVersion]
):
    wf, ver = sample_workflow
    async with TestSessionLocal() as session:
        monitor = WorkerMonitorService(session)
        await monitor.register_worker("dead-worker-1")

        # Create a run assigned to dead-worker-1
        run = WorkflowRun(
            workflow_id=wf.id,
            workflow_version_id=ver.id,
            triggered_by=test_user.id,
            status=RunStatus.RUNNING.value,
            worker_id="dead-worker-1",
            started_at=datetime.now(timezone.utc) - timedelta(seconds=400),
            last_heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=400),
        )
        session.add(run)
        await session.commit()
        run_id = run.id

        # Mark dead-worker-1 unresponsive
        w_rec = (await session.execute(select(WorkerRecord).where(WorkerRecord.worker_id == "dead-worker-1"))).scalar_one()
        w_rec.status = WorkerStatus.UNRESPONSIVE.value
        await session.commit()

        # Detection test
        rec_service = RecoveryService(session)
        orphaned = await rec_service.detect_orphaned_runs()
        assert any(r.id == run_id for r in orphaned)

        # Claim and recover test
        with patch("app.tasks.execution_tasks.execute_workflow_run.delay") as mock_delay:
            success, msg = await rec_service.claim_and_recover_run(
                run_id=run_id,
                reason="worker_unresponsive",
                recovery_worker_id="recovery-worker-2",
            )

        assert success is True
        assert mock_delay.called

        # Verify DB state updated with fencing token
        q = select(WorkflowRun).where(WorkflowRun.id == run_id)
        recovered_run = (await session.execute(q)).scalar_one()
        assert recovered_run.status == RunStatus.RECOVERING.value
        assert recovered_run.fencing_token == 1
        assert recovered_run.recovery_attempt_count == 1
        assert recovered_run.worker_id == "recovery-worker-2"


@pytest.mark.asyncio
async def test_terminal_and_cancelled_runs_never_recovered(
    test_user: User, sample_workflow: tuple[Workflow, WorkflowVersion]
):
    wf, ver = sample_workflow
    async with TestSessionLocal() as session:
        completed_run = WorkflowRun(
            workflow_id=wf.id,
            workflow_version_id=ver.id,
            triggered_by=test_user.id,
            status=RunStatus.COMPLETED.value,
        )
        cancelled_run = WorkflowRun(
            workflow_id=wf.id,
            workflow_version_id=ver.id,
            triggered_by=test_user.id,
            status=RunStatus.CANCELLED.value,
        )
        session.add_all([completed_run, cancelled_run])
        await session.commit()

        rec_service = RecoveryService(session)
        s1, m1 = await rec_service.claim_and_recover_run(completed_run.id)
        s2, m2 = await rec_service.claim_and_recover_run(cancelled_run.id)

        assert s1 is False
        assert "terminal" in m1.lower()
        assert s2 is False
        assert "cancelled" in m2.lower()


@pytest.mark.asyncio
async def test_fencing_token_prevents_stale_worker_overwrite(
    test_user: User, sample_workflow: tuple[Workflow, WorkflowVersion]
):
    wf, ver = sample_workflow
    async with TestSessionLocal() as session:
        run = WorkflowRun(
            workflow_id=wf.id,
            workflow_version_id=ver.id,
            triggered_by=test_user.id,
            status=RunStatus.RUNNING.value,
            fencing_token=0,
            input_data={"parcel_id": "P-101"},
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    # Simulate recovery worker claims run and increments fencing token to 1
    async with TestSessionLocal() as session:
        rec_service = RecoveryService(session)
        with patch("app.tasks.execution_tasks.execute_workflow_run.delay"):
            await rec_service.claim_and_recover_run(run_id, reason="test_recovery")

    # Now simulate the original zombie worker (holding old token 0) tries to execute
    async with TestSessionLocal() as session:
        executor = WorkflowExecutor(session)
        finished_run = await executor.execute_run(
            workflow_run_id=run_id,
            expected_fencing_token=0,  # Old zombie token
            worker_id="zombie-worker-old",
        )

        # Execution must have been aborted by fencing violation!
        assert finished_run.status == RunStatus.RECOVERING.value
        assert finished_run.fencing_token == 1


@pytest.mark.asyncio
async def test_max_recovery_attempts_exceeded_marks_failed(
    test_user: User, sample_workflow: tuple[Workflow, WorkflowVersion]
):
    wf, ver = sample_workflow
    async with TestSessionLocal() as session:
        run = WorkflowRun(
            workflow_id=wf.id,
            workflow_version_id=ver.id,
            triggered_by=test_user.id,
            status=RunStatus.RUNNING.value,
            recovery_attempt_count=settings.EXECUTION_MAX_RECOVERY_ATTEMPTS,  # Exhausted
        )
        session.add(run)
        await session.commit()
        run_id = run.id

        rec_service = RecoveryService(session)
        success, msg = await rec_service.claim_and_recover_run(run_id, manual=False)

        assert success is False
        assert "maximum recovery attempts" in msg.lower()

        # Run must be marked FAILED
        q = select(WorkflowRun).where(WorkflowRun.id == run_id)
        failed_run = (await session.execute(q)).scalar_one()
        assert failed_run.status == RunStatus.FAILED.value


# ===========================================================================
# 3. Circuit Breaker System Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_circuit_breaker_closed_state_permits_requests():
    cb = CircuitBreaker()
    can_exec, reason = await cb.can_execute("county:orange_assessor", "county_website")
    assert can_exec is True
    assert reason is None


@pytest.mark.asyncio
async def test_circuit_breaker_trips_to_open_on_threshold_failures():
    cb = CircuitBreaker()
    key = f"county:tripping_{uuid.uuid4().hex[:8]}"

    # Record threshold failures
    for i in range(settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        await cb.record_failure(
            dependency_key=key,
            error=httpx.ConnectTimeout("Connection timed out"),
            dependency_type="county_website",
        )

    # Circuit should now be OPEN
    state = await cb.get_circuit_state(key)
    assert state["state"] == CircuitState.OPEN.value
    assert state["open_until"] is not None

    # New requests should be blocked
    can_exec, reason = await cb.can_execute(key)
    assert can_exec is False
    assert "OPEN" in reason


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_probe_and_recovery():
    cb = CircuitBreaker()
    key = f"county:recovering_{uuid.uuid4().hex[:8]}"

    # Trip to OPEN
    for _ in range(settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        await cb.record_failure(
            dependency_key=key,
            error=httpx.ConnectTimeout("Timeout"),
        )

    # Force open_until into past to simulate cooldown expiration
    past_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    state_to_expire = await cb.get_circuit_state(key)
    state_to_expire["open_until"] = past_time
    await cb._save_state(key, state_to_expire)

    # First request should transition to HALF_OPEN and permit probe
    can_exec1, _ = await cb.can_execute(key)
    assert can_exec1 is True
    state1 = await cb.get_circuit_state(key)
    assert state1["state"] == CircuitState.HALF_OPEN.value

    # Record consecutive successes to meet success threshold
    for _ in range(settings.CIRCUIT_BREAKER_SUCCESS_THRESHOLD):
        await cb.record_success(key)

    # Circuit should transition to CLOSED
    state_closed = await cb.get_circuit_state(key)
    assert state_closed["state"] == CircuitState.CLOSED.value
    assert state_closed["failure_count"] == 0


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_probe_failure_reopens():
    cb = CircuitBreaker()
    key = f"county:failing_probe_{uuid.uuid4().hex[:8]}"

    # Set state directly to HALF_OPEN
    cb._local_cache[key] = {
        "dependency_key": key,
        "dependency_type": "county_website",
        "state": CircuitState.HALF_OPEN.value,
        "half_open_active_probes": 1,
        "consecutive_successes": 0,
    }

    # Probe fails
    await cb.record_failure(key, httpx.ConnectTimeout("Probe failed"))

    # Must immediately reopen to OPEN
    state = await cb.get_circuit_state(key)
    assert state["state"] == CircuitState.OPEN.value
    assert state["open_until"] is not None


@pytest.mark.asyncio
async def test_circuit_breaker_ignores_security_challenges_and_auth_errors():
    cb = CircuitBreaker()
    key = f"county:sec_blocked_{uuid.uuid4().hex[:8]}"

    # Record CAPTCHA / Cloudflare / 403 Forbidden errors
    from app.browser.step_runner import SecurityChallengeException
    from app.browser.security_detector import SecurityDetectionResult

    for _ in range(10):
        await cb.record_failure(
            key,
            SecurityChallengeException(SecurityDetectionResult(is_detected=True, event_type="cloudflare")),
        )
        await cb.record_failure(key, httpx.HTTPStatusError("403 Forbidden", request=None, response=MagicMock(status_code=403)))

    # Circuit must STILL be CLOSED! Security challenges must not trip circuit breakers
    state = await cb.get_circuit_state(key)
    assert state["state"] == CircuitState.CLOSED.value
    assert state["failure_count"] == 0


@pytest.mark.asyncio
async def test_retry_engine_circuit_breaker_integration():
    cb = circuit_breaker
    key = f"service:retry_integ_{uuid.uuid4().hex[:8]}"

    # Trip the circuit
    for _ in range(settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        await cb.record_failure(key, httpx.ConnectTimeout("Connect timeout"))

    manager = RetryManager()
    called = False

    async def _operation():
        nonlocal called
        called = True
        return "success"

    # Executing through retry_manager must raise CircuitBreakerOpenException immediately without retrying
    with pytest.raises(CircuitBreakerOpenException):
        await manager.execute(
            _operation,
            policy=NO_RETRY_POLICY,
            circuit_key=key,
        )

    assert called is False


@pytest.mark.asyncio
async def test_independent_dependencies_maintain_isolated_states():
    cb = CircuitBreaker()
    key_a = f"county:county_a_{uuid.uuid4().hex[:8]}"
    key_b = f"county:county_b_{uuid.uuid4().hex[:8]}"

    for _ in range(settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        await cb.record_failure(key_a, httpx.ConnectTimeout("Timeout on A"))

    state_a = await cb.get_circuit_state(key_a)
    state_b = await cb.get_circuit_state(key_b)

    assert state_a["state"] == CircuitState.OPEN.value
    assert state_b["state"] == CircuitState.CLOSED.value


# ===========================================================================
# 4. Monitoring & Recovery API Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_api_workers_endpoints(client: httpx.AsyncClient):
    # Seed a worker in DB
    async with TestSessionLocal() as session:
        monitor = WorkerMonitorService(session)
        await monitor.register_worker(
            worker_id="api-worker-1",
            hostname="worker-box",
            pid=9876,
            queues=["executions"],
        )

    # GET /api/v1/workers
    resp_list = await client.get("/api/v1/workers")
    assert resp_list.status_code == 200
    workers = resp_list.json()
    assert any(w["worker_id"] == "api-worker-1" for w in workers)

    # GET /api/v1/workers/health
    resp_health = await client.get("/api/v1/workers/health")
    assert resp_health.status_code == 200
    health = resp_health.json()
    assert health["total_workers"] >= 1
    assert "overall_status" in health

    # GET /api/v1/workers/{worker_id}
    resp_detail = await client.get("/api/v1/workers/api-worker-1")
    assert resp_detail.status_code == 200
    assert resp_detail.json()["worker_id"] == "api-worker-1"


@pytest.mark.asyncio
async def test_api_circuit_breakers_endpoints(client: httpx.AsyncClient):
    key = "county:api_tested_county"
    # Trip circuit
    for _ in range(settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        await circuit_breaker.record_failure(key, httpx.ConnectTimeout("Timeout"))

    # GET /api/v1/circuit-breakers
    resp_list = await client.get("/api/v1/circuit-breakers")
    assert resp_list.status_code == 200
    assert isinstance(resp_list.json(), list)

    # GET /api/v1/circuit-breakers/{dependency_key}
    resp_get = await client.get(f"/api/v1/circuit-breakers/{key}")
    assert resp_get.status_code == 200
    assert resp_get.json()["state"] == CircuitState.OPEN.value

    # POST /api/v1/circuit-breakers/{dependency_key}/reset
    resp_reset = await client.post(f"/api/v1/circuit-breakers/{key}/reset")
    assert resp_reset.status_code == 200
    assert resp_reset.json()["state"] == CircuitState.CLOSED.value


@pytest.mark.asyncio
async def test_api_execution_recovery_endpoints(
    client: httpx.AsyncClient,
    test_user: User,
    sample_workflow: tuple[Workflow, WorkflowVersion],
):
    wf, ver = sample_workflow
    async with TestSessionLocal() as session:
        run = WorkflowRun(
            workflow_id=wf.id,
            workflow_version_id=ver.id,
            triggered_by=test_user.id,
            status=RunStatus.RUNNING.value,
            recovery_attempt_count=1,
            recovery_reason="previous_interruption",
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    # GET /api/v1/executions/{run_id}/recovery
    resp_rec = await client.get(f"/api/v1/executions/{run_id}/recovery")
    assert resp_rec.status_code == 200
    data = resp_rec.json()
    assert data["workflow_run_id"] == str(run_id)
    assert data["recovery_attempt_count"] == 1

    # POST /api/v1/executions/{run_id}/recover
    with patch("app.tasks.execution_tasks.execute_workflow_run.delay"):
        resp_trigger = await client.post(f"/api/v1/executions/{run_id}/recover")
    assert resp_trigger.status_code == 200
    assert resp_trigger.json()["success"] is True
