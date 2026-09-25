"""What Changed engine: explain every difference between two analyses of the same ticker."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Mapping

from marketlens.domain.decision import DecisionThresholds


@dataclass(frozen=True, slots=True)
class AnalysisDigest:
    ticker: str
    as_of: datetime
    score: float
    components: Mapping[str, float]
    action: str | None
    price: float | None
    in_buy_zone: bool | None
    stop_breached: bool | None
    rr: float | None
    eps_revision_30d: float | None
    revenue_revision_30d: float | None
    last_earnings_date: date | None
    guidance_signature: str | None
    issue_ids: tuple[str, ...]
    major_issue_ids: tuple[str, ...]
    regime: str | None
    us10y: float | None
    thesis_invalidated: bool
    agent_stances: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChangeItem:
    kind: str
    text: str
    material: bool
    magnitude: float | None = None


def diff(prev: AnalysisDigest | None, cur: AnalysisDigest, th: DecisionThresholds | None = None) -> list[ChangeItem]:
    th = th or DecisionThresholds()
    if prev is None:
        return [ChangeItem("initial", "first analysis for this ticker", True)]
    out: list[ChangeItem] = []
    if cur.last_earnings_date and cur.last_earnings_date != prev.last_earnings_date:
        out.append(ChangeItem("earnings", f"new earnings report ({cur.last_earnings_date.isoformat()})", True))
    if cur.guidance_signature and cur.guidance_signature != prev.guidance_signature:
        out.append(ChangeItem("guidance", "new guidance", True))
    for label, a, b in (("EPS revision 30D", prev.eps_revision_30d, cur.eps_revision_30d), ("revenue revision 30D", prev.revenue_revision_30d, cur.revenue_revision_30d)):
        if a is not None and b is not None and abs(b - a) >= th.material_revision_delta:
            out.append(ChangeItem("revision", f"{label} {a * 100:+.1f}% → {b * 100:+.1f}%", True, b - a))
        elif a is not None and b is not None and abs(b - a) >= 0.001:
            out.append(ChangeItem("revision", f"{label} {a * 100:+.1f}% → {b * 100:+.1f}%", False, b - a))
    new_major = set(cur.major_issue_ids) - set(prev.major_issue_ids)
    for i in sorted(new_major):
        out.append(ChangeItem("issue", f"new major issue {i}", True))
    new_minor = (set(cur.issue_ids) - set(prev.issue_ids)) - new_major
    if new_minor:
        out.append(ChangeItem("issue", f"{len(new_minor)} new minor issue(s)", False))
    if cur.regime and prev.regime and cur.regime != prev.regime:
        out.append(ChangeItem("regime", f"macro regime {prev.regime} → {cur.regime}", True))
    if cur.us10y is not None and prev.us10y is not None and abs(cur.us10y - prev.us10y) >= 0.05:
        out.append(ChangeItem("macro", f"US10Y {(cur.us10y - prev.us10y) * 100:+.0f}bp", abs(cur.us10y - prev.us10y) >= 0.25, cur.us10y - prev.us10y))
    if prev.in_buy_zone is not None and cur.in_buy_zone is not None and prev.in_buy_zone != cur.in_buy_zone:
        out.append(ChangeItem("price_zone", "price entered the buy zone" if cur.in_buy_zone else "price left the buy zone", True))
    if cur.stop_breached and not prev.stop_breached:
        out.append(ChangeItem("price_zone", "price broke below the stop level", True))
    if cur.thesis_invalidated and not prev.thesis_invalidated:
        out.append(ChangeItem("thesis", "thesis invalidation triggered", True))
    if prev.rr is not None and cur.rr is not None and abs(cur.rr - prev.rr) >= 0.1:
        out.append(ChangeItem("rr", f"R/R {prev.rr:.1f} → {cur.rr:.1f}", abs(cur.rr - prev.rr) >= th.material_rr_delta, cur.rr - prev.rr))
    ds = cur.score - prev.score
    if abs(ds) >= 0.5:
        out.append(ChangeItem("score", f"score {prev.score:.1f} → {cur.score:.1f}", abs(ds) >= th.material_score_delta, ds))
    for comp, v in cur.components.items():
        pv = prev.components.get(comp)
        if pv is not None and abs(v - pv) >= 0.05:
            out.append(ChangeItem("component", f"{comp} subscore {pv:.2f} → {v:.2f}", False, v - pv))
    if prev.price and cur.price:
        mv = cur.price / prev.price - 1
        if abs(mv) >= 0.005:
            out.append(ChangeItem("price", f"price {prev.price:.2f} → {cur.price:.2f} ({mv * 100:+.1f}%)", False, mv))
    for agent, stance in cur.agent_stances.items():
        ps = prev.agent_stances.get(agent)
        if ps and ps != stance:
            out.append(ChangeItem("agent", f"{agent}: {ps} → {stance}", False))
    if prev.action and cur.action and prev.action != cur.action:
        out.append(ChangeItem("action", f"recommendation {prev.action} → {cur.action}", False))
    return out


def material_reasons(changes: list[ChangeItem]) -> tuple[str, ...]:
    return tuple(c.text for c in changes if c.material)
