"""Issue intelligence: structured events, causal mechanisms and time-horizon impact.

A headline's sentiment is never an investment signal on its own. An issue becomes an impact only through
(1) an explicit causal mechanism, (2) the company's exposure (direct or via the graph),
(3) a per-horizon profile and (4) a priced-in adjustment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from marketlens.domain.enums import ConfirmedStatus, Horizon, IssueCategory
from marketlens.domain.exposure_graph import ExposureGraph, HopDecay


@dataclass(frozen=True, slots=True)
class IssueEffect:
    """Primary (first-order) effect of an issue on a node, e.g. NVDA: -0.6 via China TAM."""

    node_id: str
    direction: float  # [-1, 1]
    mechanism: tuple[str, ...]  # causal chain steps
    origin_impact: bool = True  # False when the origin is only a transmitter (e.g. a customer raising capex)


@dataclass(frozen=True, slots=True)
class Issue:
    issue_id: str
    title: str
    category: IssueCategory
    summary: str
    event_time: datetime
    publish_time: datetime
    sources: tuple[str, ...]
    source_quality: float  # 0..1 (official > wire > blog)
    confirmed_status: ConfirmedStatus
    affected_sectors: tuple[str, ...]
    primary_effects: tuple[IssueEffect, ...]
    importance: float  # 0..1
    surprise_factor: float  # 0..1 (how unexpected)
    market_awareness: float  # 0..1 (repetition / coverage)
    confidence: float  # 0..1
    evidence_id: str | None = None

    @property
    def affected_companies(self) -> tuple[str, ...]:
        return tuple(e.node_id for e in self.primary_effects)


# Per-category horizon profile: how much of the effect shows up in each horizon.
HORIZON_PROFILE: Mapping[IssueCategory, Mapping[Horizon, float]] = {
    IssueCategory.EARNINGS: {Horizon.IMMEDIATE: 1.0, Horizon.SHORT: 0.8, Horizon.SWING: 0.5, Horizon.FUNDAMENTAL: 0.6},
    IssueCategory.GUIDANCE: {Horizon.IMMEDIATE: 1.0, Horizon.SHORT: 0.8, Horizon.SWING: 0.7, Horizon.FUNDAMENTAL: 0.8},
    IssueCategory.EXPORT_CONTROL: {Horizon.IMMEDIATE: 0.7, Horizon.SHORT: 0.6, Horizon.SWING: 0.7, Horizon.FUNDAMENTAL: 1.0},
    IssueCategory.REGULATION: {Horizon.IMMEDIATE: 0.5, Horizon.SHORT: 0.5, Horizon.SWING: 0.6, Horizon.FUNDAMENTAL: 0.9},
    IssueCategory.TARIFF: {Horizon.IMMEDIATE: 0.8, Horizon.SHORT: 0.6, Horizon.SWING: 0.6, Horizon.FUNDAMENTAL: 0.8},
    IssueCategory.GEOPOLITICS: {Horizon.IMMEDIATE: 0.9, Horizon.SHORT: 0.6, Horizon.SWING: 0.4, Horizon.FUNDAMENTAL: 0.4},
    IssueCategory.AI: {Horizon.IMMEDIATE: 0.6, Horizon.SHORT: 0.6, Horizon.SWING: 0.7, Horizon.FUNDAMENTAL: 0.9},
    IssueCategory.MA: {Horizon.IMMEDIATE: 1.0, Horizon.SHORT: 0.7, Horizon.SWING: 0.5, Horizon.FUNDAMENTAL: 0.6},
    IssueCategory.PRODUCT: {Horizon.IMMEDIATE: 0.5, Horizon.SHORT: 0.5, Horizon.SWING: 0.6, Horizon.FUNDAMENTAL: 0.8},
    IssueCategory.COMPETITION: {Horizon.IMMEDIATE: 0.5, Horizon.SHORT: 0.5, Horizon.SWING: 0.6, Horizon.FUNDAMENTAL: 0.9},
    IssueCategory.SUPPLY_CHAIN: {Horizon.IMMEDIATE: 0.6, Horizon.SHORT: 0.6, Horizon.SWING: 0.7, Horizon.FUNDAMENTAL: 0.7},
    IssueCategory.RATES: {Horizon.IMMEDIATE: 1.0, Horizon.SHORT: 0.7, Horizon.SWING: 0.5, Horizon.FUNDAMENTAL: 0.4},
    IssueCategory.INFLATION: {Horizon.IMMEDIATE: 1.0, Horizon.SHORT: 0.7, Horizon.SWING: 0.5, Horizon.FUNDAMENTAL: 0.4},
    IssueCategory.OIL: {Horizon.IMMEDIATE: 0.9, Horizon.SHORT: 0.7, Horizon.SWING: 0.6, Horizon.FUNDAMENTAL: 0.5},
    IssueCategory.LEGAL: {Horizon.IMMEDIATE: 0.6, Horizon.SHORT: 0.4, Horizon.SWING: 0.4, Horizon.FUNDAMENTAL: 0.6},
    IssueCategory.ANTITRUST: {Horizon.IMMEDIATE: 0.6, Horizon.SHORT: 0.4, Horizon.SWING: 0.5, Horizon.FUNDAMENTAL: 0.8},
    IssueCategory.FINANCING: {Horizon.IMMEDIATE: 0.8, Horizon.SHORT: 0.6, Horizon.SWING: 0.4, Horizon.FUNDAMENTAL: 0.5},
    IssueCategory.DILUTION: {Horizon.IMMEDIATE: 1.0, Horizon.SHORT: 0.6, Horizon.SWING: 0.4, Horizon.FUNDAMENTAL: 0.6},
    IssueCategory.BUYBACK: {Horizon.IMMEDIATE: 0.7, Horizon.SHORT: 0.5, Horizon.SWING: 0.5, Horizon.FUNDAMENTAL: 0.5},
    IssueCategory.MANAGEMENT: {Horizon.IMMEDIATE: 0.7, Horizon.SHORT: 0.5, Horizon.SWING: 0.5, Horizon.FUNDAMENTAL: 0.6},
    IssueCategory.CYBERSECURITY: {Horizon.IMMEDIATE: 0.8, Horizon.SHORT: 0.6, Horizon.SWING: 0.4, Horizon.FUNDAMENTAL: 0.4},
}

# Priced-in mostly neutralises short-horizon impact; fundamental effects still matter.
PRICED_IN_DAMPING: Mapping[Horizon, float] = {
    Horizon.IMMEDIATE: 0.9,
    Horizon.SHORT: 0.8,
    Horizon.SWING: 0.5,
    Horizon.FUNDAMENTAL: 0.1,
}

STATUS_WEIGHT: Mapping[str, float] = {"CONFIRMED": 1.0, "REPORTED": 0.7, "RUMOR": 0.35}


@dataclass(frozen=True, slots=True)
class HorizonImpact:
    horizon: Horizon
    direction: int  # -1, 0, +1
    impact_score: float  # -100..+100
    confidence: float  # 0..1
    mechanism: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompanyIssueImpact:
    issue_id: str
    ticker: str
    hops: int
    exposure_path: tuple[str, ...]
    horizons: tuple[HorizonImpact, ...]
    priced_in: float | None  # 0..100 estimate

    def at(self, h: Horizon) -> HorizonImpact:
        return next(x for x in self.horizons if x.horizon == h)


@dataclass(frozen=True, slots=True)
class ImpactConfig:
    max_hops: int = 2
    decay: HopDecay = field(default_factory=HopDecay)
    min_abs_multiplier: float = 0.05


def compute_issue_impacts(
    issue: Issue,
    graph: ExposureGraph,
    priced_in: Mapping[str, float] | None = None,
    cfg: ImpactConfig | None = None,
) -> list[CompanyIssueImpact]:
    cfg = cfg or ImpactConfig()
    priced_in = priced_in or {}
    profile = HORIZON_PROFILE[issue.category]
    status_w = STATUS_WEIGHT[issue.confirmed_status.value]
    base_strength = issue.importance * (0.5 + 0.5 * issue.surprise_factor) * status_w
    results: dict[str, CompanyIssueImpact] = {}
    for eff in issue.primary_effects:
        paths = graph.propagate([eff.node_id], max_hops=cfg.max_hops, decay=cfg.decay)
        for target, p in paths.items():
            if p.hops == 0 and not eff.origin_impact:
                continue
            node = graph.nodes.get(target)
            if node is not None and node.node_type.value != "Company":
                continue
            if node is None and target != eff.node_id:
                continue
            mult = eff.direction * p.multiplier
            if abs(mult) < cfg.min_abs_multiplier:
                continue
            pi = priced_in.get(target)
            mech = list(eff.mechanism)
            if p.hops > 0:
                hop_desc = " → ".join(
                    f"{a} [{et.value}] {b}" for a, b, et in zip(p.path[:-1], p.path[1:], p.edge_types)
                )
                mech.append(f"transmitted via {hop_desc} ({p.hops}-hop)")
            horizons: list[HorizonImpact] = []
            for h in (Horizon.IMMEDIATE, Horizon.SHORT, Horizon.SWING, Horizon.FUNDAMENTAL):
                val = mult * base_strength * profile[h]
                if pi is not None:
                    val *= 1 - (pi / 100.0) * PRICED_IN_DAMPING[h]
                score = max(-100.0, min(100.0, val * 100))
                direction = 1 if score > 2 else -1 if score < -2 else 0
                conf = issue.confidence * p.confidence * issue.source_quality
                horizons.append(HorizonImpact(h, direction, round(score, 2), round(conf, 4), tuple(mech)))
            imp = CompanyIssueImpact(issue.issue_id, target, p.hops, p.path, tuple(horizons), pi)
            cur = results.get(target)
            if cur is None or abs(imp.at(Horizon.SWING).impact_score) > abs(cur.at(Horizon.SWING).impact_score):
                results[target] = imp
    return sorted(results.values(), key=lambda x: -abs(x.at(Horizon.SWING).impact_score))


def aggregate_issue_score(impacts: list[CompanyIssueImpact], horizon: Horizon = Horizon.SWING) -> float:
    """Net impact across issues for one ticker, bounded to [-100, 100]."""
    total = sum(i.at(horizon).impact_score * max(0.2, i.at(horizon).confidence) for i in impacts)
    return max(-100.0, min(100.0, total))


# Category → default causal chain templates used when an issue is structured by rules.
CAUSAL_TEMPLATES: Mapping[IssueCategory, tuple[str, ...]] = {
    IssueCategory.EXPORT_CONTROL: ("addressable market in restricted region ↓", "revenue opportunity ↓", "EPS revision risk ↑", "valuation pressure"),
    IssueCategory.TARIFF: ("input/import cost ↑", "gross margin pressure", "EPS revision risk ↑"),
    IssueCategory.AI: ("AI infrastructure demand ↑", "order/backlog visibility ↑", "revenue estimates ↑"),
    IssueCategory.RATES: ("discount rate ↑", "long-duration equity multiples ↓"),
    IssueCategory.INFLATION: ("inflation expectations ↑", "policy-rate path ↑", "multiple pressure"),
    IssueCategory.OIL: ("energy prices ↑", "producer revenue ↑ / consumer & transport costs ↑"),
    IssueCategory.EARNINGS: ("reported results vs consensus", "estimate revisions", "re-rating"),
    IssueCategory.GUIDANCE: ("outlook vs consensus", "forward estimate revisions", "re-rating"),
    IssueCategory.ANTITRUST: ("business-practice constraint risk", "long-term margin/growth risk"),
    IssueCategory.DILUTION: ("share count ↑", "per-share value ↓"),
    IssueCategory.BUYBACK: ("share count ↓", "per-share value ↑"),
    IssueCategory.SUPPLY_CHAIN: ("supply constraint", "shipment timing risk", "near-term revenue risk"),
    IssueCategory.COMPETITION: ("competitive intensity ↑", "share/pricing pressure"),
    IssueCategory.MA: ("deal terms", "strategic fit / financing", "re-rating"),
}
