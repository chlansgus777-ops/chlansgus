"""Load the curated exposure graph and macro sensitivities."""

from __future__ import annotations

import tomllib
from datetime import date
from pathlib import Path

from marketlens.config import CONFIG_DIR
from marketlens.domain.enums import EdgeType, NodeType
from marketlens.domain.exposure_graph import Edge, ExposureGraph, Node
from marketlens.domain.macro import MacroExposure
from marketlens.domain.market import Security


class GraphSeed:
    def __init__(self, path: Path | None = None) -> None:
        p = path or (CONFIG_DIR / "exposure_graph.toml")
        d = tomllib.loads(p.read_text(encoding="utf-8"))
        self.version: str = d["version"]
        updated = date.fromisoformat(d["last_updated"])
        src = d["source"]
        self.nodes = [Node(n["id"], NodeType(n["type"]), n["label"]) for n in d["nodes"]]
        self.edges = [
            Edge(e["src"], e["dst"], EdgeType(e["type"]), float(e["weight"]), float(e["confidence"]), e.get("source", src), updated)
            for e in d["edges"]
        ]
        self._explicit: dict[str, dict[str, float]] = d.get("macro_exposure", {})
        self._sector: dict[str, dict[str, float]] = d.get("sector_defaults", {})
        self._industry: dict[str, dict[str, float]] = d.get("industry_defaults", {})

    def graph(self, securities: list[Security]) -> ExposureGraph:
        company_nodes = [Node(s.ticker, NodeType.COMPANY, s.company_name) for s in securities]
        known = {n.node_id for n in self.nodes} | {n.node_id for n in company_nodes}
        # edges may reference companies outside the current universe: add them as company nodes too
        for e in self.edges:
            for nid in (e.src, e.dst):
                if nid not in known and ":" not in nid:
                    company_nodes.append(Node(nid, NodeType.COMPANY, nid))
                    known.add(nid)
        return ExposureGraph(self.nodes + company_nodes, self.edges)

    def subgraph(self, graph: ExposureGraph, ticker: str, hops: int = 2) -> tuple[list[Node], list[Edge]]:
        frontier = {ticker}
        seen = {ticker}
        for _ in range(hops):
            nxt: set[str] = set()
            for n in frontier:
                for nb, _e in graph.neighbors(n):
                    if nb not in seen:
                        nxt.add(nb)
            seen |= nxt
            frontier = nxt
        edges = [e for e in graph.edges if e.src in seen and e.dst in seen]
        nodes = [graph.nodes[n] for n in seen if n in graph.nodes]
        return nodes, edges

    def macro_exposure(self, sec: Security, beta: float | None = None) -> MacroExposure:
        vals: dict[str, float] = {}
        vals.update(self._sector.get(sec.sector, {}))
        for k, v in self._industry.items():
            if k.lower() in (sec.industry or "").lower():
                vals.update(v)
        vals.update(self._explicit.get(sec.ticker, {}))
        return MacroExposure(
            rates=float(vals.get("rates", 0.0)),
            oil=float(vals.get("oil", 0.0)),
            usd=float(vals.get("usd", 0.0)),
            ai=float(vals.get("ai", 0.0)),
            beta=beta if beta is not None else 1.0,
        )
