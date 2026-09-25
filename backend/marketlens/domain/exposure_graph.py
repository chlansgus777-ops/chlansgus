"""Company exposure graph and issue propagation (default max 2 hops)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from marketlens.domain.enums import EdgeType, NodeType


@dataclass(frozen=True, slots=True)
class Node:
    node_id: str  # e.g. "NVDA", "COUNTRY:CN", "COMMODITY:OIL", "THEME:AI"
    node_type: NodeType
    label: str


@dataclass(frozen=True, slots=True)
class Edge:
    src: str
    dst: str
    edge_type: EdgeType
    weight: float  # 0..1 strength of dependency (signed for macro exposures)
    confidence: float  # 0..1
    source: str
    last_updated: date


# How an effect on ``src`` transmits to ``dst`` across an edge (sign multiplier).
# e.g. MSFT CUSTOMER_OF-> NVDA stored as NVDA SUPPLIER_OF MSFT: MSFT demand ↑ → NVDA ↑ (+1).
# A competitor's positive news is mildly negative (share shift).
TRANSMISSION: dict[EdgeType, float] = {
    EdgeType.SUPPLIER_OF: 1.0,
    EdgeType.CUSTOMER_OF: 1.0,
    EdgeType.PARTNER_OF: 0.8,
    EdgeType.DEPENDS_ON: 1.0,
    EdgeType.PROVIDES_TO: 1.0,
    EdgeType.COMPETITOR_OF: -0.5,
    EdgeType.EXPOSED_TO_REGION: 1.0,
    EdgeType.EXPOSED_TO_RATE: 1.0,
    EdgeType.EXPOSED_TO_COMMODITY: 1.0,
    EdgeType.EXPOSED_TO_AI: 1.0,
    EdgeType.EXPOSED_TO_CLOUD: 1.0,
}


@dataclass(frozen=True, slots=True)
class PropagationPath:
    target: str
    hops: int
    path: tuple[str, ...]
    edge_types: tuple[EdgeType, ...]
    multiplier: float  # signed, includes hop decay, weights, confidences
    confidence: float


@dataclass(frozen=True, slots=True)
class HopDecay:
    direct: float = 1.0
    one_hop: float = 0.65
    two_hop: float = 0.35

    def for_hops(self, h: int) -> float:
        return {0: self.direct, 1: self.one_hop, 2: self.two_hop}.get(h, 0.0)


class ExposureGraph:
    def __init__(self, nodes: list[Node], edges: list[Edge]) -> None:
        self.nodes = {n.node_id: n for n in nodes}
        # undirected traversal: effects travel both ways along commercial relationships
        self._adj: dict[str, list[tuple[str, Edge]]] = defaultdict(list)
        for e in edges:
            self._adj[e.src].append((e.dst, e))
            self._adj[e.dst].append((e.src, e))
        self.edges = list(edges)

    def neighbors(self, node_id: str) -> list[tuple[str, Edge]]:
        return list(self._adj.get(node_id, []))

    def exposures_of(self, ticker: str, edge_type: EdgeType) -> list[Edge]:
        return [e for e in self.edges if e.src == ticker and e.edge_type == edge_type]

    def propagate(self, origins: list[str], max_hops: int = 2, decay: HopDecay | None = None) -> dict[str, PropagationPath]:
        """Best (largest |multiplier|) path from any origin to each reachable node within ``max_hops``."""
        decay = decay or HopDecay()
        best: dict[str, PropagationPath] = {}
        frontier: list[PropagationPath] = []
        for o in origins:
            p = PropagationPath(o, 0, (o,), (), decay.direct, 1.0)
            best[o] = p
            frontier.append(p)
        for hop in range(1, max_hops + 1):
            nxt: list[PropagationPath] = []
            for p in frontier:
                raw_mult = p.multiplier / decay.for_hops(p.hops) if decay.for_hops(p.hops) else 0.0
                for nb, e in self.neighbors(p.target):
                    if nb in p.path:
                        continue
                    sign = TRANSMISSION.get(e.edge_type, 0.0)
                    if sign == 0:
                        continue
                    m = raw_mult * sign * e.weight * decay.for_hops(hop)
                    conf = p.confidence * e.confidence
                    cand = PropagationPath(nb, hop, p.path + (nb,), p.edge_types + (e.edge_type,), m, conf)
                    cur = best.get(nb)
                    if cur is None or abs(cand.multiplier) > abs(cur.multiplier):
                        best[nb] = cand
                        nxt.append(cand)
            frontier = nxt
        return best
