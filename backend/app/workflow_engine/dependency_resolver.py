"""DAG dependency analysis and topological level resolution."""

from collections import defaultdict, deque
from typing import Any


class DependencyResolver:
    """Resolves DAG dependencies and groups nodes into concurrent execution waves."""

    def __init__(self, definition: dict[str, Any]) -> None:
        self.definition = definition
        self.nodes = definition.get("nodes", [])
        self.edges = definition.get("edges", [])
        self.node_map = {str(n.get("id")): n for n in self.nodes}

        self.adj = defaultdict(list)
        self.in_degree = {nid: 0 for nid in self.node_map}
        self.predecessors = defaultdict(list)

        for edge in self.edges:
            src = str(edge.get("source", ""))
            tgt = str(edge.get("target", ""))
            if src in self.node_map and tgt in self.node_map:
                self.adj[src].append(tgt)
                self.predecessors[tgt].append(src)
                self.in_degree[tgt] += 1

    def get_topological_order(self) -> list[str]:
        """Returns linear topological order of node IDs."""
        in_deg = dict(self.in_degree)
        queue = deque([nid for nid, deg in in_deg.items() if deg == 0])
        order: list[str] = []

        while queue:
            curr = queue.popleft()
            order.append(curr)
            for nxt in self.adj[curr]:
                in_deg[nxt] -= 1
                if in_deg[nxt] == 0:
                    queue.append(nxt)

        if len(order) != len(self.node_map):
            raise ValueError("Cycle detected in workflow graph definition.")
        return order

    def get_execution_waves(self) -> list[list[dict[str, Any]]]:
        """
        Groups nodes into sequential 'waves' (layers).
        Nodes within the same wave have no dependencies between each other
        and can be executed concurrently (e.g. independent Assessor and Tax branches).
        """
        in_deg = dict(self.in_degree)
        current_wave = [nid for nid, deg in in_deg.items() if deg == 0]
        waves: list[list[dict[str, Any]]] = []

        while current_wave:
            waves.append([self.node_map[nid] for nid in current_wave])
            next_wave: list[str] = []
            for nid in current_wave:
                for nxt in self.adj[nid]:
                    in_deg[nxt] -= 1
                    if in_deg[nxt] == 0:
                        next_wave.append(nxt)
            current_wave = next_wave

        return waves

