"""Workflow execution orchestrator managing database state transitions and persistence."""

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.models.execution import NodeRun, WorkflowRun
from app.models.result import AssessorResult, TaxResult
from app.models.security_event import SecurityEvent
from app.models.workflow import WorkflowVersion
from app.workflow_engine.execution_context import ExecutionContext
from app.workflow_engine.execution_state import can_transition_run
from app.workflow_engine.scheduler import WorkflowScheduler

logger = get_logger(__name__)


class WorkflowExecutor:
    """Executes a WorkflowRun with full database transaction and lifecycle management."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def execute_run(
        self,
        workflow_run_id: uuid.UUID,
        expected_fencing_token: int | None = None,
        worker_id: str | None = None,
    ) -> WorkflowRun:
        start_wall_time = time.time()
        logger.info(
            "Starting execution for WorkflowRun %s (fencing_token=%s, worker_id=%s)",
            workflow_run_id,
            expected_fencing_token,
            worker_id,
        )

        # 1. Fetch WorkflowRun with version and existing node runs
        query = (
            select(WorkflowRun)
            .where(WorkflowRun.id == workflow_run_id)
            .options(
                selectinload(WorkflowRun.node_runs),
                selectinload(WorkflowRun.workflow_version),
                selectinload(WorkflowRun.workflow),
            )
        )
        res = await self.db.execute(query)
        run = res.scalar_one_or_none()
        if not run:
            raise ValueError(f"WorkflowRun {workflow_run_id} not found.")

        # Fencing token check
        current_fence = run.fencing_token or 0
        if expected_fencing_token is not None and current_fence > expected_fencing_token:
            logger.warning(
                "Fencing violation on run %s: current token %d > expected %d. Aborting stale worker execution.",
                run.id,
                current_fence,
                expected_fencing_token,
            )
            return run

        if expected_fencing_token is not None:
            run.fencing_token = expected_fencing_token
        if worker_id:
            run.worker_id = worker_id
        run.last_heartbeat_at = datetime.now(timezone.utc)

        # 2. Check cancellation or guarded state transition to RUNNING
        if run.status == "CANCEL_REQUESTED":
            run.status = "CANCELLED"
            run.completed_at = datetime.now(timezone.utc)
            for nr in run.node_runs:
                nr.status = "CANCELLED"
            await self.db.commit()
            await self.db.refresh(run)
            return run

        if not can_transition_run(run.status, "RUNNING"):
            logger.warning("WorkflowRun %s cannot transition from %s to RUNNING", run.id, run.status)
            return run

        run.status = "RUNNING"
        run.started_at = datetime.now(timezone.utc)
        await self.db.commit()

        # 3. Load workflow definition from version
        version = run.workflow_version
        if not version or not version.definition:
            run.status = "FAILED"
            run.error_message = "Workflow version or definition is missing."
            run.completed_at = datetime.now(timezone.utc)
            await self.db.commit()
            return run

        definition = version.definition
        nodes = definition.get("nodes", [])

        # 4. Create NodeRun records if they don't already exist
        node_runs_by_nid: dict[str, NodeRun] = {}
        for nr in run.node_runs:
            node_runs_by_nid[nr.node_id] = nr

        for n in nodes:
            nid = str(n.get("id"))
            ntype = str(n.get("type")).lower()
            if nid not in node_runs_by_nid:
                nr = NodeRun(
                    workflow_run_id=run.id,
                    node_id=nid,
                    node_type=ntype,
                    status="PENDING",
                    input_data=n.get("data", {}),
                    output_data={},
                )
                self.db.add(nr)
                node_runs_by_nid[nid] = nr

        await self.db.commit()

        # 5. Initialize Context & Scheduler
        effective_county_source_id = None
        if run.workflow:
            effective_county_source_id = run.workflow.county_source_id
        if not effective_county_source_id:
            effective_county_source_id = definition.get("county_source_id")

        context = ExecutionContext(
            run_id=run.id,
            workflow_id=run.workflow_id,
            version_id=version.id,
            workflow_definition=definition,
            initial_input=run.input_data or {},
            db_session=self.db,
            county_source_id=effective_county_source_id,
        )

        # Pre-populate context with any already-completed node results (for duplicate deliveries / retries)
        for nid, nr in node_runs_by_nid.items():
            if nr.status == "COMPLETED":
                context.node_statuses[nid] = "COMPLETED"
                context.node_outputs[nid] = nr.output_data or {}
                context.node_durations[nid] = nr.duration_ms or 0
                if nr.node_type == "assessor":
                    context.assessor_success = True
                elif nr.node_type == "tax":
                    context.tax_success = True

        # Check if cancellation was requested before start
        check_cancel_q = select(WorkflowRun.status).where(WorkflowRun.id == run.id)
        current_status_res = await self.db.execute(check_cancel_q)
        current_status = current_status_res.scalar()
        if current_status == "CANCEL_REQUESTED":
            context.is_cancelled = True

        scheduler = WorkflowScheduler(context)
        exec_outcome = await scheduler.run()

        # 6. Synchronize NodeRun outcomes and Result tables
        for nid, nstatus in context.node_statuses.items():
            nr = node_runs_by_nid.get(nid)
            if not nr:
                continue
            nr.status = nstatus
            nr.output_data = context.node_outputs.get(nid, {})
            nr.error_message = context.node_errors.get(nid)
            nr.started_at = context.node_started_at.get(nid) or nr.started_at or datetime.now(timezone.utc)
            nr.completed_at = datetime.now(timezone.utc)
            act_dur = context.node_durations.get(nid, 0)
            if act_dur == 0 and nr.started_at and nr.completed_at:
                act_dur = max(1, int((nr.completed_at - nr.started_at).total_seconds() * 1000))
            nr.duration_ms = act_dur
            nr.retry_count = context.node_retry_counts.get(nid, 0)

            # Persist AssessorResult if assessor completed (guarded against duplicate insertion)
            if nr.node_type == "assessor" and nstatus == "COMPLETED":
                assessor_payload = nr.output_data.get("assessor", {})
                if assessor_payload:
                    existing_assessor = await self.db.execute(
                        select(AssessorResult).where(AssessorResult.node_run_id == nr.id)
                    )
                    if not existing_assessor.scalar_one_or_none():
                        def _parse_num(val):
                            if val is None:
                                return None
                            if isinstance(val, (int, float)):
                                return float(val)
                            import re
                            s = re.sub(r"[^\d.-]", "", str(val)).strip()
                            try:
                                return float(s) if s else None
                            except ValueError:
                                return None

                        assessor_rec = AssessorResult(
                            node_run_id=nr.id,
                            parcel_number=str(assessor_payload.get("parcel_number") or ""),
                            owner_name=assessor_payload.get("owner_name"),
                            property_address=assessor_payload.get("property_address"),
                            legal_description=assessor_payload.get("legal_description"),
                            assessed_value=_parse_num(assessor_payload.get("assessed_value")),
                            land_value=_parse_num(assessor_payload.get("land_value")),
                            improvement_value=_parse_num(assessor_payload.get("improvement_value")),
                            tax_year=int(assessor_payload.get("tax_year")) if assessor_payload.get("tax_year") and str(assessor_payload.get("tax_year")).isdigit() else None,
                            raw_data=assessor_payload.get("raw_data", {}),
                        )
                        self.db.add(assessor_rec)

            # Persist TaxResult if tax completed (guarded against duplicate insertion)
            if nr.node_type == "tax" and nstatus == "COMPLETED":
                tax_payload = nr.output_data.get("tax", {})
                if tax_payload:
                    existing_tax = await self.db.execute(
                        select(TaxResult).where(TaxResult.node_run_id == nr.id)
                    )
                    if not existing_tax.scalar_one_or_none():
                        def _parse_num(val):
                            if val is None:
                                return None
                            if isinstance(val, (int, float)):
                                return float(val)
                            import re
                            s = re.sub(r"[^\d.-]", "", str(val)).strip()
                            try:
                                return float(s) if s else None
                            except ValueError:
                                return None

                        tax_rec = TaxResult(
                            node_run_id=nr.id,
                            parcel_number=str(tax_payload.get("parcel_number") or ""),
                            tax_year=int(tax_payload.get("tax_year")) if tax_payload.get("tax_year") and str(tax_payload.get("tax_year")).isdigit() else None,
                            amount_due=_parse_num(tax_payload.get("amount_due")),
                            amount_paid=_parse_num(tax_payload.get("amount_paid")),
                            status=tax_payload.get("status"),
                            raw_data=tax_payload.get("raw_data", {}),
                        )
                        self.db.add(tax_rec)

            # Persist evidence paths in output_data if present
            if nid in context.evidence_records:
                current_out = dict(nr.output_data or {})
                current_out["_evidence"] = context.evidence_records[nid]
                nr.output_data = current_out

        # Persist SecurityEvent records if any were captured
        for sec in context.security_events:
            sec_record = SecurityEvent(
                workflow_run_id=run.id,
                county_source_id=None,
                event_type=sec.get("event_type", "other"),
                url=sec.get("url"),
                details=sec.get("details", {}),
            )
            self.db.add(sec_record)

        # 7. Finalize WorkflowRun status
        total_duration = int((time.time() - start_wall_time) * 1000)
        final_status = exec_outcome["overall_status"]

        # Check fencing token again before committing final status
        check_fence_q = select(WorkflowRun.fencing_token).where(WorkflowRun.id == run.id)
        current_fence_res = await self.db.execute(check_fence_q)
        current_fence_latest = current_fence_res.scalar() or 0
        if expected_fencing_token is not None and current_fence_latest > expected_fencing_token:
            logger.warning(
                "Finalization aborted for run %s: DB fencing_token %d > worker token %d. Worker was fenced out.",
                run.id,
                current_fence_latest,
                expected_fencing_token,
            )
            return run

        # Determine branch statuses based on configured nodes
        assessor_branch_status = "NOT_CONFIGURED"
        tax_branch_status = "NOT_CONFIGURED"

        if context.has_assessor:
            assessor_nodes = [n for n in nodes if str(n.get("type", "")).lower() == "assessor"]
            if assessor_nodes:
                a_id = str(assessor_nodes[0].get("id"))
                a_stat = context.node_statuses.get(a_id, "PENDING")
                if a_stat == "COMPLETED":
                    assessor_branch_status = "SUCCEEDED"
                elif a_stat == "FAILED":
                    assessor_branch_status = "FAILED"
                elif a_stat == "SKIPPED":
                    assessor_branch_status = "SKIPPED"
                else:
                    assessor_branch_status = "RUNNING"

        if context.has_tax:
            tax_nodes = [n for n in nodes if str(n.get("type", "")).lower() == "tax"]
            if tax_nodes:
                t_id = str(tax_nodes[0].get("id"))
                t_stat = context.node_statuses.get(t_id, "PENDING")
                if t_stat == "COMPLETED":
                    tax_branch_status = "SUCCEEDED"
                elif t_stat == "FAILED":
                    tax_branch_status = "FAILED"
                elif t_stat == "SKIPPED":
                    tax_branch_status = "SKIPPED"
                else:
                    tax_branch_status = "RUNNING"

        run.status = final_status
        run.completed_at = datetime.now(timezone.utc)
        run.duration_ms = total_duration
        run.execution_metadata = {
            "node_statuses": exec_outcome["node_statuses"],
            "errors": list(context.node_errors.values()),
            "assessor_branch_status": assessor_branch_status,
            "tax_branch_status": tax_branch_status,
            "has_assessor": context.has_assessor,
            "has_tax": context.has_tax,
            "has_normalize": context.has_normalize,
            "assessor_success": context.assessor_success,
            "tax_success": context.tax_success,
            "supports_partial_success": context.supports_partial_success,
            "evidence": context.evidence_records,
            "security_events": context.security_events,
            "final_output": exec_outcome["final_output"],
        }
        if context.node_errors:
            run.error_message = "; ".join(f"{k}: {v}" for k, v in context.node_errors.items())

        # If run had recovery attempts and completed successfully, record audit record
        if (run.recovery_attempt_count or 0) > 0 and final_status in ["COMPLETED", "PARTIALLY_COMPLETED"]:
            from app.models.recovery import RecoveryStatus, WorkflowRecoveryRecord

            succ_audit = WorkflowRecoveryRecord(
                workflow_run_id=run.id,
                recovery_worker_id=worker_id,
                fencing_token=current_fence_latest,
                attempt_number=run.recovery_attempt_count or 1,
                status=RecoveryStatus.SUCCEEDED.value,
                reason="execution_completed_successfully",
                details={"final_status": final_status, "duration_ms": total_duration},
            )
            self.db.add(succ_audit)

        await self.db.commit()
        await self.db.refresh(run)

        logger.info(
            "WorkflowRun %s finalized with status %s in %d ms",
            run.id,
            run.status,
            total_duration,
        )
        return run
