"""Execution service managing WorkflowRun submission, Celery queuing, and querying."""

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.models.execution import NodeRun, RunStatus, WorkflowRun
from app.models.workflow import Workflow, WorkflowVersion
from app.schemas.execution import (
    ExecutionResultsResponse,
    NodeRunResponse,
    WorkflowExecuteRequest,
    WorkflowRunListResponse,
    WorkflowRunResponse,
)
from app.services.workflow_validator import validate_workflow_graph
from app.workflow_engine.execution_state import TERMINAL_RUN_STATES

logger = get_logger(__name__)


class ExecutionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _to_run_response(self, run: WorkflowRun) -> WorkflowRunResponse:
        node_responses = [
            NodeRunResponse(
                id=nr.id,
                workflow_run_id=nr.workflow_run_id,
                node_id=nr.node_id,
                node_type=nr.node_type,
                status=nr.status,
                input_data=nr.input_data or {},
                output_data=nr.output_data or {},
                error_message=nr.error_message,
                retry_count=nr.retry_count,
                county_source_id=nr.county_source_id,
                recipe_version_id=nr.recipe_version_id,
                started_at=nr.started_at,
                completed_at=nr.completed_at,
                duration_ms=nr.duration_ms,
                created_at=nr.created_at,
            )
            for nr in (run.node_runs or [])
        ]

        wf_name = run.workflow.name if run.workflow else None
        ver_num = run.workflow_version.version_number if run.workflow_version else None

        return WorkflowRunResponse(
            id=run.id,
            workflow_id=run.workflow_id,
            workflow_version_id=run.workflow_version_id,
            workflow_name=wf_name,
            version_number=ver_num,
            triggered_by=run.triggered_by,
            celery_task_id=run.celery_task_id,
            status=run.status,
            input_data=run.input_data or {},
            started_at=run.started_at,
            completed_at=run.completed_at,
            duration_ms=run.duration_ms,
            error_message=run.error_message,
            execution_metadata=run.execution_metadata or {},
            created_at=run.created_at,
            node_runs=node_responses,
        )

    async def submit_execution(
        self,
        workflow_id: uuid.UUID,
        user_id: uuid.UUID,
        payload: WorkflowExecuteRequest,
        is_superuser: bool = False,
    ) -> WorkflowRunResponse:
        """Submits a workflow execution to Celery background processing."""
        # 1. Fetch workflow and versions
        wf_query = (
            select(Workflow)
            .where(Workflow.id == workflow_id)
            .options(selectinload(Workflow.versions))
        )
        res = await self.db.execute(wf_query)
        workflow = res.scalar_one_or_none()
        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workflow with ID {workflow_id} not found.",
            )

        if not is_superuser and workflow.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to execute this workflow.",
            )

        # 2. Select version
        if payload.workflow_version_id:
            target_version = next(
                (v for v in workflow.versions if v.id == payload.workflow_version_id), None
            )
            if not target_version:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Workflow version {payload.workflow_version_id} not found for this workflow.",
                )
        else:
            target_version = next((v for v in workflow.versions if v.is_current), None)
            if not target_version and workflow.versions:
                target_version = workflow.versions[0]

        if not target_version or not target_version.definition:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Workflow has no executable version definition.",
            )

        # 3. Validate DAG
        val = validate_workflow_graph(target_version.definition)
        if not val["is_valid"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot execute invalid workflow graph: {'; '.join(val['errors'])}",
            )

        # 4. Create WorkflowRun in DB
        run = WorkflowRun(
            workflow_id=workflow.id,
            workflow_version_id=target_version.id,
            triggered_by=user_id,
            status=RunStatus.PENDING.value,
            input_data=payload.input_data or {},
            execution_metadata={"topological_order": val["execution_order"]},
        )
        self.db.add(run)
        await self.db.commit()
        await self.db.refresh(run)

        # 5. Dispatch to Celery background worker
        task_id = None
        try:
            from app.tasks.execution_tasks import execute_workflow_run

            async_res = execute_workflow_run.delay(str(run.id))
            task_id = str(async_res.id) if async_res and hasattr(async_res, "id") else None
            run.celery_task_id = task_id
            run.status = RunStatus.QUEUED.value
            await self.db.commit()
            await self.db.refresh(run)
        except Exception as exc:
            logger.warning(
                "Celery broker dispatch unavailable (%s). Execution %s queued for manual or worker execution.",
                exc,
                run.id,
            )
            # Retain PENDING/QUEUED state
            run.status = RunStatus.QUEUED.value
            await self.db.commit()

        # Re-fetch with relations
        return await self.get_execution(run.id, user_id, is_superuser)


    async def list_executions(
        self,
        user_id: uuid.UUID,
        is_superuser: bool = False,
        workflow_id: uuid.UUID | None = None,
        status_filter: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> WorkflowRunListResponse:
        query = (
            select(WorkflowRun)
            .options(
                selectinload(WorkflowRun.workflow),
                selectinload(WorkflowRun.workflow_version),
                selectinload(WorkflowRun.node_runs),
            )
        )
        count_query = select(func.count(WorkflowRun.id))

        if not is_superuser:
            query = query.where(WorkflowRun.triggered_by == user_id)
            count_query = count_query.where(WorkflowRun.triggered_by == user_id)

        if workflow_id is not None:
            query = query.where(WorkflowRun.workflow_id == workflow_id)
            count_query = count_query.where(WorkflowRun.workflow_id == workflow_id)

        if status_filter:
            query = query.where(WorkflowRun.status == status_filter.upper())
            count_query = count_query.where(WorkflowRun.status == status_filter.upper())

        total_res = await self.db.execute(count_query)
        total = total_res.scalar() or 0

        query = query.order_by(WorkflowRun.created_at.desc()).offset(skip).limit(limit)
        res = await self.db.execute(query)
        runs = res.scalars().all()

        return WorkflowRunListResponse(
            items=[self._to_run_response(r) for r in runs],
            total=total,
        )

    async def get_execution(
        self,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        is_superuser: bool = False,
    ) -> WorkflowRunResponse:
        query = (
            select(WorkflowRun)
            .where(WorkflowRun.id == run_id)
            .options(
                selectinload(WorkflowRun.workflow),
                selectinload(WorkflowRun.workflow_version),
                selectinload(WorkflowRun.node_runs),
            )
        )
        res = await self.db.execute(query)
        run = res.scalar_one_or_none()
        if not run:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"WorkflowRun {run_id} not found.",
            )

        if not is_superuser and run.triggered_by != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view this execution.",
            )

        return self._to_run_response(run)

    async def get_execution_results(
        self,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        is_superuser: bool = False,
    ) -> ExecutionResultsResponse:
        run = await self.get_execution(run_id, user_id, is_superuser)

        metadata = run.execution_metadata or {}
        final_out = metadata.get("final_output", {}).get("result", {})

        wf_nodes = []
        if run.workflow_version_id:
            from app.models.workflow import WorkflowVersion
            wv_stmt = select(WorkflowVersion).where(WorkflowVersion.id == run.workflow_version_id)
            wv_res = await self.db.execute(wv_stmt)
            wv = wv_res.scalar_one_or_none()
            if wv and wv.definition and isinstance(wv.definition, dict):
                wf_nodes = wv.definition.get("nodes", [])

        has_assessor = any(str(n.get("type", "")).lower() == "assessor" for n in wf_nodes)
        has_tax = any(str(n.get("type", "")).lower() == "tax" for n in wf_nodes)
        has_normalize = any(str(n.get("type", "")).lower() == "normalize" for n in wf_nodes)

        assessor_res = None
        tax_res = None
        norm_res = None

        assessor_nr = None
        tax_nr = None

        for nr in run.node_runs:
            if nr.node_type == "assessor":
                assessor_nr = nr
                if nr.output_data:
                    assessor_res = nr.output_data.get("assessor")
            elif nr.node_type == "tax":
                tax_nr = nr
                if nr.output_data:
                    tax_res = nr.output_data.get("tax")
            elif nr.node_type == "normalize" and nr.output_data:
                norm_res = nr.output_data.get("normalized")

        # Determine Assessor Branch Status
        if not has_assessor:
            assessor_branch_status = "NOT_CONFIGURED"
        elif metadata.get("assessor_branch_status"):
            assessor_branch_status = metadata["assessor_branch_status"]
        elif assessor_nr:
            if assessor_nr.status == "COMPLETED":
                assessor_branch_status = "SUCCEEDED"
            elif assessor_nr.status == "FAILED":
                assessor_branch_status = "FAILED"
            elif assessor_nr.status == "SKIPPED":
                assessor_branch_status = "SKIPPED"
            else:
                assessor_branch_status = "RUNNING"
        else:
            if run.status in ("QUEUED", "PENDING"):
                assessor_branch_status = "QUEUED"
            elif run.status == "RUNNING":
                assessor_branch_status = "RUNNING"
            else:
                assessor_branch_status = "SKIPPED"

        # Determine Tax Branch Status
        if not has_tax:
            tax_branch_status = "NOT_CONFIGURED"
        elif metadata.get("tax_branch_status"):
            tax_branch_status = metadata["tax_branch_status"]
        elif tax_nr:
            if tax_nr.status == "COMPLETED":
                tax_branch_status = "SUCCEEDED"
            elif tax_nr.status == "FAILED":
                tax_branch_status = "FAILED"
            elif tax_nr.status == "SKIPPED":
                tax_branch_status = "SKIPPED"
            else:
                tax_branch_status = "RUNNING"
        else:
            if run.status in ("QUEUED", "PENDING"):
                tax_branch_status = "QUEUED"
            elif run.status == "RUNNING":
                tax_branch_status = "RUNNING"
            else:
                tax_branch_status = "SKIPPED"

        # Calculate records counts
        assessor_records_count = 0
        if assessor_res and isinstance(assessor_res, dict):
            recs = assessor_res.get("_records")
            if isinstance(recs, list):
                assessor_records_count = len(recs)
            elif assessor_res.get("parcel_number") or assessor_res.get("owner_name") or assessor_res.get("property_address"):
                assessor_records_count = 1

        tax_records_count = 0
        if tax_res and isinstance(tax_res, dict):
            if tax_res.get("parcel_number") or tax_res.get("amount_due") is not None:
                tax_records_count = 1

        return ExecutionResultsResponse(
            workflow_run_id=run.id,
            workflow_id=run.workflow_id,
            workflow_version_id=run.workflow_version_id,
            status=run.status,
            duration_ms=run.duration_ms,
            is_partial_success=metadata.get("supports_partial_success", False)
            and run.status == "PARTIALLY_COMPLETED",
            assessor_branch_status=assessor_branch_status,
            tax_branch_status=tax_branch_status,
            has_assessor_branch=has_assessor,
            has_tax_branch=has_tax,
            has_normalize_branch=has_normalize,
            assessor_records_count=assessor_records_count,
            tax_records_count=tax_records_count,
            assessor_result=assessor_res,
            tax_result=tax_res,
            normalized_result=norm_res,
            final_output=final_out or None,
            errors=metadata.get("errors", []),
        )

    async def list_node_runs(
        self,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        is_superuser: bool = False,
    ) -> list[NodeRunResponse]:
        run = await self.get_execution(run_id, user_id, is_superuser)
        wf_nodes = []
        if run.workflow_version_id:
            from app.models.workflow import WorkflowVersion
            wv_stmt = select(WorkflowVersion).where(WorkflowVersion.id == run.workflow_version_id)
            wv_res = await self.db.execute(wv_stmt)
            wv = wv_res.scalar_one_or_none()
            if wv and wv.definition and isinstance(wv.definition, dict):
                wf_nodes = wv.definition.get("nodes", [])

        if wf_nodes:
            order_map = {str(n.get("id")): idx for idx, n in enumerate(wf_nodes)}
            return sorted(run.node_runs, key=lambda nr: order_map.get(nr.node_id, 999))
        return sorted(run.node_runs, key=lambda nr: nr.started_at or nr.created_at)

    async def cancel_execution(
        self,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        is_superuser: bool = False,
    ) -> WorkflowRunResponse:
        query = select(WorkflowRun).where(WorkflowRun.id == run_id)
        res = await self.db.execute(query)
        run = res.scalar_one_or_none()
        if not run:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"WorkflowRun {run_id} not found.",
            )

        if not is_superuser and run.triggered_by != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to cancel this execution.",
            )

        if run.status in TERMINAL_RUN_STATES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot cancel execution in terminal state '{run.status}'.",
            )

        # Set status to CANCEL_REQUESTED
        run.status = RunStatus.CANCEL_REQUESTED.value
        await self.db.commit()

        # If Celery task is active, revoke it
        if run.celery_task_id:
            try:
                from app.tasks.celery_app import celery_app

                celery_app.control.revoke(run.celery_task_id, terminate=False)
            except Exception as e:
                logger.warning("Could not revoke Celery task %s: %s", run.celery_task_id, e)

        return await self.get_execution(run.id, user_id, is_superuser)

    async def get_execution_documents(
        self,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        is_superuser: bool = False,
    ) -> list[Any]:
        from app.models.document_artifact import DocumentArtifact

        # Verify execution exists and user has access
        await self.get_execution(run_id, user_id, is_superuser)

        query = (
            select(DocumentArtifact)
            .where(DocumentArtifact.workflow_run_id == run_id)
            .order_by(DocumentArtifact.created_at.asc())
        )
        res = await self.db.execute(query)
        return list(res.scalars().all())

