"""Script to execute a live visible Playwright browser run on Collier County Assraiser."""

import asyncio
import os
import sys
import uuid
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Force visible browser mode for this execution
os.environ["PLAYWRIGHT_HEADLESS"] = "false"
os.environ["PLAYWRIGHT_BROWSER_MODE"] = "local_visible"
os.environ["PLAYWRIGHT_SLOW_MO_MS"] = "300"
os.environ["PLAYWRIGHT_KEEP_BROWSER_OPEN_SECONDS"] = "3"

from sqlalchemy import select

from app.core.config import settings
settings.PLAYWRIGHT_HEADLESS = False
settings.PLAYWRIGHT_BROWSER_MODE = "local_visible"
settings.PLAYWRIGHT_SLOW_MO_MS = 300
settings.PLAYWRIGHT_KEEP_BROWSER_OPEN_SECONDS = 3

from app.db.session import AsyncSessionLocal
from app.models import Workflow, WorkflowRun, WorkflowVersion, User, NodeRun, AssessorResult
from app.models.browser_session import BrowserSessionRecord
from app.workflow_engine.executor import WorkflowExecutor
from app.services.execution_service import ExecutionService


async def run_live() -> None:
    async with AsyncSessionLocal() as session:
        # Find Assessor Workflow
        wf_stmt = select(Workflow).where(Workflow.name == "Assessor Workflow")
        wf = (await session.execute(wf_stmt)).scalar_one_or_none()
        if not wf:
            print("Assessor Workflow not found!")
            return

        wv_stmt = (
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == wf.id)
            .order_by(WorkflowVersion.version_number.desc())
        )
        wv = (await session.execute(wv_stmt)).scalars().first()
        if not wv:
            print("WorkflowVersion not found!")
            return

        user_stmt = select(User).limit(1)
        user = (await session.execute(user_stmt)).scalar_one_or_none()

        run_id = uuid.uuid4()
        run = WorkflowRun(
            id=run_id,
            workflow_id=wf.id,
            workflow_version_id=wv.id,
            status="PENDING",
            input_data={"owner_name": "Smith", "parcel_id": "37540640003"},
            triggered_by=user.id if user else uuid.uuid4(),
        )
        session.add(run)
        await session.commit()
        print(f"Created WorkflowRun: {run_id}")

    # Execute workflow run
    async with AsyncSessionLocal() as session:
        executor = WorkflowExecutor(session)
        print("Starting WorkflowExecutor.execute_run with visible Playwright browser...")
        finished_run = await executor.execute_run(run_id)
        print(f"WorkflowRun finished with status: {finished_run.status}, duration: {finished_run.duration_ms}ms")

    # Fetch ExecutionResultsResponse and Browser Activity
    async with AsyncSessionLocal() as session:
        exec_service = ExecutionService(session)
        results = await exec_service.get_execution_results(
            run_id, user_id=user.id if user else uuid.uuid4(), is_superuser=True
        )
        print("\n--- EXECUTION RESULTS ---")
        print(f"Status: {results.status}")
        print(f"Duration: {results.duration_ms}ms")
        print(f"Assessor Branch Status: {results.assessor_branch_status}")
        print(f"Tax Branch Status: {results.tax_branch_status}")
        print(f"Has Assessor Branch: {results.has_assessor_branch}")
        print(f"Has Tax Branch: {results.has_tax_branch}")
        print(f"Assessor Records Count: {results.assessor_records_count}")
        print(f"Assessor Result Summary: {results.assessor_result}")

        # Check NodeRuns
        node_runs_stmt = select(NodeRun).where(NodeRun.workflow_run_id == run_id)
        node_runs = (await session.execute(node_runs_stmt)).scalars().all()
        print("\n--- NODE RUN TIMELINE ---")
        for nr in node_runs:
            print(f"Node: {nr.node_id} ({nr.node_type}) | Status: {nr.status} | Duration: {nr.duration_ms}ms | Started: {nr.started_at}")

        # Check BrowserSessionRecord
        sess_stmt = select(BrowserSessionRecord).where(BrowserSessionRecord.workflow_run_id == run_id)
        sess = (await session.execute(sess_stmt)).scalar_one_or_none()
        if sess:
            print(f"\n--- BROWSER SESSION & ACTIVITY LOG ({sess.session_id}) ---")
            print(f"Browser State: {sess.state} | Healthy: {sess.is_healthy}")
            print(f"Current URL: {sess.current_url}")
            print("Activity Log Entries:")
            for act in (sess.activity_log or []):
                print(f"  [{act.get('status')}] {act.get('action')} - {act.get('description')} ({act.get('duration_ms')}ms)")


if __name__ == "__main__":
    asyncio.run(run_live())

