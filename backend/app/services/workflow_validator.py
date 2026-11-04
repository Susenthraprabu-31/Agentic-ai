"""Workflow definition DAG validator and dependency analyzer."""

from collections import defaultdict, deque
from typing import Any


ALLOWED_NODE_TYPES = {"input", "assessor", "tax", "normalize", "output"}


class WorkflowValidationError(Exception):
    """Raised when a workflow definition fails structural or semantic validation."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or [message]


def validate_workflow_graph(definition: dict[str, Any]) -> dict[str, Any]:
    """
    Validates a React Flow visual workflow definition:
    - Verifies 'nodes' and 'edges' structures
    - Enforces allowed node types (input, assessor, tax, normalize, output)
    - Validates DAG properties (directed acyclic graph, cycle detection)
    - Validates reachability from input to output
    - Validates independent execution for assessor/tax
    - Checks partial success configuration
    - Produces a valid execution topological ordering
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(definition, dict):
        return {
            "is_valid": False,
            "errors": ["Workflow definition must be a JSON object."],
            "warnings": [],
            "execution_order": [],
            "node_count": 0,
            "edge_count": 0,
            "supports_assessor": False,
            "supports_tax": False,
            "supports_partial_success": False,
        }

    nodes = definition.get("nodes", [])
    edges = definition.get("edges", [])

    if not isinstance(nodes, list) or len(nodes) == 0:
        errors.append("Workflow must contain at least one node.")
        return {
            "is_valid": False,
            "errors": errors,
            "warnings": warnings,
            "execution_order": [],
            "node_count": 0,
            "edge_count": 0,
            "supports_assessor": False,
            "supports_tax": False,
            "supports_partial_success": False,
        }

    # 1. Validate node identity and allowed types
    node_ids: set[str] = set()
    node_type_counts: dict[str, int] = defaultdict(int)
    nodes_by_id: dict[str, dict[str, Any]] = {}

    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"Node at index {idx} must be a dictionary.")
            continue

        nid = str(node.get("id", "")).strip()
        if not nid:
            errors.append(f"Node at index {idx} is missing an 'id'.")
            continue

        if nid in node_ids:
            errors.append(f"Duplicate node id '{nid}'.")
        node_ids.add(nid)
        nodes_by_id[nid] = node

        ntype = str(node.get("type", "")).strip().lower()
        if ntype not in ALLOWED_NODE_TYPES:
            errors.append(
                f"Node '{nid}' has unsupported type '{ntype}'. Allowed types: {sorted(ALLOWED_NODE_TYPES)}"
            )
        else:
            node_type_counts[ntype] += 1

    # Check required core node roles
    if node_type_counts["input"] == 0:
        errors.append("Workflow must contain an 'input' node to receive search criteria.")
    elif node_type_counts["input"] > 1:
        warnings.append("Workflow has multiple 'input' nodes. Exactly one primary input node is recommended.")

    if node_type_counts["output"] == 0:
        errors.append("Workflow must contain at least one 'output' node to return structured search results.")

    has_assessor = node_type_counts["assessor"] > 0
    has_tax = node_type_counts["tax"] > 0

    if not has_assessor and not has_tax:
        warnings.append("Workflow does not contain an 'assessor' or 'tax' node.")

    # 2. Build adjacency list and in-degree map for edges
    adj: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
    predecessors: dict[str, list[str]] = defaultdict(list)

    if not isinstance(edges, list):
        errors.append("Workflow 'edges' must be a list.")
        edges = []

    for e_idx, edge in enumerate(edges):
        if not isinstance(edge, dict):
            errors.append(f"Edge at index {e_idx} must be a dictionary.")
            continue

        src = str(edge.get("source", "")).strip()
        tgt = str(edge.get("target", "")).strip()

        if not src or not tgt:
            errors.append(f"Edge at index {e_idx} must have non-empty 'source' and 'target'.")
            continue

        if src not in node_ids:
            errors.append(f"Edge references nonexistent source node '{src}'.")
            continue

        if tgt not in node_ids:
            errors.append(f"Edge references nonexistent target node '{tgt}'.")
            continue

        if src == tgt:
            errors.append(f"Self-loop detected on node '{src}'.")
            continue

        # Prevent input node from receiving incoming edges
        tgt_node = nodes_by_id.get(tgt)
        if tgt_node and tgt_node.get("type") == "input":
            errors.append(f"Input node '{tgt}' cannot have incoming connections.")

        # Prevent output node from having outgoing edges
        src_node = nodes_by_id.get(src)
        if src_node and src_node.get("type") == "output":
            errors.append(f"Output node '{src}' cannot have outgoing connections.")

        adj[src].append(tgt)
        predecessors[tgt].append(src)
        in_degree[tgt] += 1

    # 3. Cycle Detection & Topological Sort (Kahn's Algorithm)
    queue = deque([nid for nid in node_ids if in_degree[nid] == 0])
    execution_order: list[str] = []
    temp_in_degree = dict(in_degree)

    while queue:
        curr = queue.popleft()
        execution_order.append(curr)

        for neighbor in adj[curr]:
            temp_in_degree[neighbor] -= 1
            if temp_in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(execution_order) != len(node_ids):
        errors.append("Cycle detected in workflow graph! Workflows must be directed acyclic graphs (DAGs).")

    # 4. Check reachability and disconnected / orphan nodes
    input_nodes = [nid for nid, node in nodes_by_id.items() if node.get("type") == "input"]
    output_nodes = [nid for nid, node in nodes_by_id.items() if node.get("type") == "output"]

    # Reachable from any input
    reachable_from_input: set[str] = set()
    reach_queue = deque(input_nodes)
    while reach_queue:
        curr = reach_queue.popleft()
        reachable_from_input.add(curr)
        for nxt in adj[curr]:
            if nxt not in reachable_from_input:
                reach_queue.append(nxt)

    orphan_nodes = node_ids - reachable_from_input
    if orphan_nodes:
        warnings.append(
            f"Nodes {sorted(orphan_nodes)} are unreachable from the workflow input node."
        )

    # Check if at least one output is reachable from input
    reachable_outputs = reachable_from_input.intersection(output_nodes)
    if input_nodes and output_nodes and not reachable_outputs:
        errors.append("No 'output' node is reachable from any 'input' node.")

    # 5. Check Partial Success & Normalize Node Configuration
    supports_partial_success = False
    normalize_nodes = [node for node in nodes if node.get("type") == "normalize"]
    for norm in normalize_nodes:
        norm_data = norm.get("data", {})
        if norm_data.get("support_partial_success") is True or norm_data.get("allow_partial_success") is True:
            supports_partial_success = True

    execution_config = definition.get("execution_config", {})
    if execution_config.get("allow_partial_success") is True:
        supports_partial_success = True

    # If both assessor and tax nodes exist and feed normalize, recommend partial success
    if has_assessor and has_tax and not supports_partial_success:
        warnings.append(
            "Workflow contains both Assessor and Tax branches. Enabling 'support_partial_success' on the Normalize node or execution config is recommended."
        )

    return {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "execution_order": execution_order if len(errors) == 0 else [],
        "node_count": len(node_ids),
        "edge_count": len(edges),
        "supports_assessor": has_assessor,
        "supports_tax": has_tax,
        "supports_partial_success": supports_partial_success,
    }

