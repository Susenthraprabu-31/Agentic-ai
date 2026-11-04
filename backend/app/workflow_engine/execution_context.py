"""Execution context holding in-memory runtime data, node outputs, and status."""

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NodeExecutionResult:
    """Result returned by a NodeExecutor."""
    status: str  # COMPLETED, FAILED, SKIPPED, CANCELLED
    output_data: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    duration_ms: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)
    security_event: dict[str, Any] | None = None
    retry_count: int = 0


class ExecutionContext:
    """Stateful context for a single WorkflowRun execution."""

    def __init__(
        self,
        run_id: uuid.UUID,
        workflow_id: uuid.UUID,
        version_id: uuid.UUID,
        workflow_definition: dict[str, Any],
        initial_input: dict[str, Any],
        db_session: Any | None = None,
        county_source_id: uuid.UUID | None = None,
    ) -> None:
        self.run_id = run_id
        self.workflow_id = workflow_id
        self.version_id = version_id
        self.county_source_id = county_source_id or workflow_definition.get("county_source_id")
        self.definition = workflow_definition
        self.initial_input = initial_input
        self.db_session = db_session

        # Node storage
        self.node_outputs: dict[str, dict[str, Any]] = {}
        self.node_statuses: dict[str, str] = {}
        self.node_errors: dict[str, str] = {}
        self.node_durations: dict[str, int] = {}
        self.node_retry_counts: dict[str, int] = {}
        self.node_started_at: dict[str, Any] = {}

        # Upstream edge mapping: target_node_id -> list of source_node_ids
        self.incoming_edges: dict[str, list[str]] = {}
        edges = workflow_definition.get("edges", [])
        for edge in edges:
            src = str(edge.get("source", ""))
            tgt = str(edge.get("target", ""))
            if tgt:
                self.incoming_edges.setdefault(tgt, []).append(src)

        # Global flags
        self.is_cancelled: bool = False
        self.has_assessor: bool = False
        self.has_tax: bool = False
        self.has_normalize: bool = False
        self.assessor_success: bool = False
        self.tax_success: bool = False
        self.supports_partial_success: bool = False

        self.evidence_records: dict[str, dict[str, Any]] = {}
        self.security_events: list[dict[str, Any]] = []

        self._analyze_definition()

    def _analyze_definition(self) -> None:
        nodes = self.definition.get("nodes", [])
        for n in nodes:
            ntype = str(n.get("type", "")).lower()
            if ntype == "assessor":
                self.has_assessor = True
            elif ntype == "tax":
                self.has_tax = True
            elif ntype == "normalize":
                self.has_normalize = True
                data = n.get("data", {})
                if data.get("support_partial_success") is True or data.get("allow_partial_success") is True:
                    self.supports_partial_success = True

        exec_cfg = self.definition.get("execution_config", {})
        if exec_cfg.get("allow_partial_success") is True:
            self.supports_partial_success = True

    def record_node_result(self, node_id: str, node_type: str, result: NodeExecutionResult) -> None:
        self.node_statuses[node_id] = result.status
        self.node_durations[node_id] = result.duration_ms
        self.node_retry_counts[node_id] = result.retry_count
        if result.evidence:
            self.evidence_records[node_id] = result.evidence
        if result.security_event:
            self.security_events.append(result.security_event)

        if result.status == "COMPLETED":
            self.node_outputs[node_id] = result.output_data
            if node_type == "assessor":
                self.assessor_success = True
            elif node_type == "tax":
                self.tax_success = True
        elif result.status == "FAILED":
            if result.error_message:
                self.node_errors[node_id] = result.error_message

    def get_upstream_outputs(self, node_id: str) -> dict[str, Any]:
        """Aggregate all outputs from immediate upstream predecessor nodes."""
        predecessors = self.incoming_edges.get(node_id, [])
        aggregated: dict[str, Any] = {}
        for p in predecessors:
            if p in self.node_outputs:
                aggregated.update(self.node_outputs[p])
        return aggregated

    def is_prerequisite_satisfied(self, node: dict[str, Any]) -> tuple[bool, str | None]:
        """
        Check if all upstream prerequisites for a node are satisfied.
        If a predecessor failed, checks if that predecessor's failure is tolerable.
        """
        node_id = str(node.get("id", ""))
        predecessors = self.incoming_edges.get(node_id, [])
        if not predecessors:
            return True, None

        for p_id in predecessors:
            p_status = self.node_statuses.get(p_id, "PENDING")
            if p_status in ("PENDING", "RUNNING", "WAITING"):
                return False, f"Upstream node '{p_id}' has not completed."
            if p_status in ("CANCELLED", "SKIPPED"):
                return False, f"Upstream node '{p_id}' was {p_status}."
            if p_status == "FAILED":
                # Find predecessor node definition to inspect allow_failure
                pred_node = next((n for n in self.definition.get("nodes", []) if n.get("id") == p_id), None)
                allow_fail = False
                if pred_node:
                    allow_fail = pred_node.get("data", {}).get("allow_failure", False)

                # If current node is normalize and supports partial success, allow predecessor failure
                current_type = str(node.get("type", "")).lower()
                if current_type == "normalize" and self.supports_partial_success:
                    continue

                if not allow_fail:
                    return False, f"Required upstream node '{p_id}' failed."

        return True, None

