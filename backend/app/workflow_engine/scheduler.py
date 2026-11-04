"""Workflow scheduler coordinating wave-based concurrent execution of DAG nodes."""

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.workflow_engine.dependency_resolver import DependencyResolver
from app.workflow_engine.execution_context import ExecutionContext, NodeExecutionResult
from app.workflow_engine.node_executor import get_node_executor

logger = get_logger(__name__)


class WorkflowScheduler:
    """Schedules and executes nodes wave-by-wave according to DAG dependencies."""

    def __init__(self, context: ExecutionContext) -> None:
        self.context = context
        self.resolver = DependencyResolver(context.definition)

    async def execute_node(self, node: dict[str, Any]) -> NodeExecutionResult:
        """Executes a single node or skips it if prerequisites are unsatisfied."""
        node_id = str(node.get("id", ""))
        node_type = str(node.get("type", "")).lower()

        # 0. Idempotency check: if node was already COMPLETED for this workflow run, reuse result
        if self.context.node_statuses.get(node_id) == "COMPLETED":
            logger.info("Node %s already COMPLETED for run %s. Reusing cached output.", node_id, self.context.run_id)
            return NodeExecutionResult(
                status="COMPLETED",
                output_data=self.context.node_outputs.get(node_id, {}),
                duration_ms=self.context.node_durations.get(node_id, 0),
                retry_count=self.context.node_retry_counts.get(node_id, 0),
            )

        # 1. Cancellation check
        if self.context.is_cancelled:
            result = NodeExecutionResult(
                status="CANCELLED",
                error_message="Execution cancelled before node started.",
            )
            self.context.record_node_result(node_id, node_type, result)
            return result

        # 2. Prerequisite check
        satisfied, reason = self.context.is_prerequisite_satisfied(node)
        if not satisfied:
            logger.info("Skipping node %s: %s", node_id, reason)
            result = NodeExecutionResult(
                status="SKIPPED",
                error_message=reason or "Prerequisites not met.",
            )
            self.context.record_node_result(node_id, node_type, result)
            return result

        # 3. Execute node
        node_start_dt = datetime.now(timezone.utc)
        self.context.node_started_at[node_id] = node_start_dt
        try:
            executor = get_node_executor(node_type)
            result = await executor.execute(node, self.context)
        except Exception as exc:
            logger.exception("Error executing node %s (%s)", node_id, node_type)
            result = NodeExecutionResult(
                status="FAILED",
                error_message=f"Node execution exception: {str(exc)}",
            )

        self.context.record_node_result(node_id, node_type, result)
        return result

    async def run(self) -> dict[str, Any]:
        """
        Executes all nodes wave by wave.
        Independent branches (e.g. Assessor and Tax) within the same wave run concurrently via asyncio.gather.
        """
        waves = self.resolver.get_execution_waves()
        logger.info("Executing workflow run %s across %d waves", self.context.run_id, len(waves))

        for wave_idx, wave in enumerate(waves):
            if self.context.is_cancelled:
                # Cancel remaining nodes in subsequent waves
                for node in wave:
                    nid = str(node.get("id"))
                    ntype = str(node.get("type")).lower()
                    self.context.record_node_result(
                        nid, ntype, NodeExecutionResult(status="CANCELLED", error_message="Run cancelled.")
                    )
                continue

            # Execute wave concurrently
            tasks = [self.execute_node(node) for node in wave]
            await asyncio.gather(*tasks)

        # Calculate final overall run status
        all_statuses = list(self.context.node_statuses.values())

        if self.context.is_cancelled:
            overall_status = "CANCELLED"
        elif all(s == "COMPLETED" for s in all_statuses):
            overall_status = "COMPLETED"
        elif any(s == "COMPLETED" for s in all_statuses) and self.context.supports_partial_success:
            # Check if any normalize or output node completed
            terminal_types = {"normalize", "output"}
            terminal_ids = {
                str(n.get("id"))
                for n in self.context.definition.get("nodes", [])
                if str(n.get("type", "")).lower() in terminal_types
            }
            if any(nid in self.context.node_outputs for nid in terminal_ids):
                overall_status = "PARTIALLY_COMPLETED"
            else:
                overall_status = "FAILED"
        else:
            overall_status = "FAILED"

        final_node_id = next(
            (
                str(n["id"])
                for n in self.context.definition.get("nodes", [])
                if str(n.get("type", "")).lower() == "output"
            ),
            "",
        )

        return {
            "overall_status": overall_status,
            "node_statuses": self.context.node_statuses,
            "node_outputs": self.context.node_outputs,
            "node_errors": self.context.node_errors,
            "final_output": self.context.node_outputs.get(final_node_id, {}),
        }
