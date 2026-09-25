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
    news_ids: tuple[str, ...] = ()  # source articles clustered into this issue

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
                mech.append(f"{hop_desc} 경로로 전달 ({p.hops}단계)")
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
    IssueCategory.EXPORT_CONTROL: ("규제 지역 판매 가능 시장 ↓", "매출 기회 ↓", "EPS 하향 위험 ↑", "밸류에이션 압박"),
    IssueCategory.TARIFF: ("원가·수입 비용 ↑", "매출총이익률 압박", "EPS 하향 위험 ↑"),
    IssueCategory.AI: ("AI 인프라 수요 ↑", "수주·백로그 가시성 ↑", "매출 추정치 ↑"),
    IssueCategory.RATES: ("할인율 ↑", "장기 성장주 멀티플 ↓"),
    IssueCategory.INFLATION: ("인플레이션 기대 ↑", "정책금리 경로 ↑", "멀티플 압박"),
    IssueCategory.OIL: ("에너지 가격 ↑", "생산자 매출 ↑ / 소비·운송 비용 ↑"),
    IssueCategory.EARNINGS: ("실적 vs 컨센서스", "추정치 수정", "재평가"),
    IssueCategory.GUIDANCE: ("가이던스 vs 컨센서스", "선행 추정치 수정", "재평가"),
    IssueCategory.ANTITRUST: ("영업 관행 제약 위험", "장기 마진·성장 위험"),
    IssueCategory.DILUTION: ("주식 수 ↑", "주당 가치 ↓"),
    IssueCategory.BUYBACK: ("주식 수 ↓", "주당 가치 ↑"),
    IssueCategory.SUPPLY_CHAIN: ("공급 제약", "출하 시점 위험", "단기 매출 위험"),
    IssueCategory.COMPETITION: ("경쟁 강도 ↑", "점유율·가격 압박"),
    IssueCategory.MA: ("거래 조건", "전략 적합성·자금 조달", "재평가"),
}
