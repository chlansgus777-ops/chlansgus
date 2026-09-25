"""What Changed engine: explain every difference between two analyses of the same ticker.

Cumulative drift is caught by comparing the score with ``baseline_score`` — the score at the time the
recommendation last changed — not only with the immediately previous run. Buy-zone flips only count as
material when the price moved decisively (≥ ``zone_buffer_atr`` ATR) past the max-buy level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Mapping

from marketlens.domain.decision import DecisionThresholds

ZONE_BUFFER_ATR = 0.25


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
    baseline_score: float | None = None  # score when the recommendation last changed
    stop: float | None = None  # stop level of this analysis' plan
    max_buy: float | None = None
    atr: float | None = None


@dataclass(frozen=True, slots=True)
class ChangeItem:
    kind: str
    text: str
    material: bool
    magnitude: float | None = None


def _decisive_zone_flip(prev: AnalysisDigest, cur: AnalysisDigest) -> bool:
    if cur.price is None or cur.max_buy is None or not cur.atr:
        return True
    return abs(cur.price - cur.max_buy) >= ZONE_BUFFER_ATR * cur.atr


def diff(prev: AnalysisDigest | None, cur: AnalysisDigest, th: DecisionThresholds | None = None) -> list[ChangeItem]:
    th = th or DecisionThresholds()
    if prev is None:
        return [ChangeItem("initial", "이 종목의 첫 분석", True)]
    out: list[ChangeItem] = []
    if cur.last_earnings_date and cur.last_earnings_date != prev.last_earnings_date:
        out.append(ChangeItem("earnings", f"새 실적 발표 ({cur.last_earnings_date.isoformat()})", True))
    if cur.guidance_signature and cur.guidance_signature != prev.guidance_signature:
        out.append(ChangeItem("guidance", "새 가이던스 발표", True))
    for label, a, b in (("EPS 추정치 30일 변화", prev.eps_revision_30d, cur.eps_revision_30d), ("매출 추정치 30일 변화", prev.revenue_revision_30d, cur.revenue_revision_30d)):
        if a is not None and b is not None and abs(b - a) >= th.material_revision_delta:
            out.append(ChangeItem("revision", f"{label} {a * 100:+.1f}% → {b * 100:+.1f}%", True, b - a))
        elif a is not None and b is not None and abs(b - a) >= 0.001:
            out.append(ChangeItem("revision", f"{label} {a * 100:+.1f}% → {b * 100:+.1f}%", False, b - a))
    new_major = set(cur.major_issue_ids) - set(prev.major_issue_ids)
    for i in sorted(new_major):
        out.append(ChangeItem("issue", f"새 주요 이슈 {i}", True))
    new_minor = (set(cur.issue_ids) - set(prev.issue_ids)) - new_major
    if new_minor:
        out.append(ChangeItem("issue", f"새 일반 이슈 {len(new_minor)}건", False))
    if cur.regime and prev.regime and cur.regime != prev.regime:
        out.append(ChangeItem("regime", f"시장 국면 {prev.regime} → {cur.regime}", True))
    if cur.us10y is not None and prev.us10y is not None and abs(cur.us10y - prev.us10y) >= 0.05:
        out.append(ChangeItem("macro", f"미 10년물 금리 {(cur.us10y - prev.us10y) * 100:+.0f}bp", abs(cur.us10y - prev.us10y) >= 0.25, cur.us10y - prev.us10y))
    if prev.in_buy_zone is not None and cur.in_buy_zone is not None and prev.in_buy_zone != cur.in_buy_zone:
        out.append(ChangeItem("price_zone", "가격이 매수 구간에 진입" if cur.in_buy_zone else "가격이 매수 구간을 이탈", _decisive_zone_flip(prev, cur)))
    if cur.stop_breached and not prev.stop_breached:
        out.append(ChangeItem("price_zone", "가격이 손절가 아래로 하락", True))
    if prev.stop is not None and cur.price is not None and prev.action in ("BUY", "BUY SMALL", "ADD") and cur.price <= prev.stop:
        out.append(ChangeItem("stop", f"직전 추천 손절가 {prev.stop:.2f} 이탈 (현재 {cur.price:.2f})", True))
    if cur.thesis_invalidated and not prev.thesis_invalidated:
        out.append(ChangeItem("thesis", "투자 논리 훼손 조건 발생", True))
    if prev.rr is not None and cur.rr is not None and abs(cur.rr - prev.rr) >= 0.1:
        out.append(ChangeItem("rr", f"손익비 {prev.rr:.1f} → {cur.rr:.1f}", abs(cur.rr - prev.rr) >= th.material_rr_delta, cur.rr - prev.rr))
    ds = cur.score - prev.score
    if abs(ds) >= 0.5:
        out.append(ChangeItem("score", f"점수 {prev.score:.1f} → {cur.score:.1f}", abs(ds) >= th.material_score_delta, ds))
    base = prev.baseline_score if prev.baseline_score is not None else prev.score
    drift = cur.score - base
    if abs(drift) >= th.material_score_delta and abs(ds) < th.material_score_delta:
        out.append(ChangeItem("score_drift", f"마지막 추천 변경 이후 누적 점수 변화 {base:.1f} → {cur.score:.1f}", True, drift))
    for comp, v in cur.components.items():
        pv = prev.components.get(comp)
        if pv is not None and abs(v - pv) >= 0.05:
            out.append(ChangeItem("component", f"{comp} 세부점수 {pv:.2f} → {v:.2f}", False, v - pv))
    if prev.price and cur.price:
        mv = cur.price / prev.price - 1
        if abs(mv) >= 0.005:
            out.append(ChangeItem("price", f"가격 {prev.price:.2f} → {cur.price:.2f} ({mv * 100:+.1f}%)", False, mv))
    for agent, stance in cur.agent_stances.items():
        ps = prev.agent_stances.get(agent)
        if ps and ps != stance:
            out.append(ChangeItem("agent", f"AI {agent}: {ps} → {stance}", False))
    if prev.action and cur.action and prev.action != cur.action:
        out.append(ChangeItem("action", f"추천 {prev.action} → {cur.action}", False))
    return out


def material_reasons(changes: list[ChangeItem]) -> tuple[str, ...]:
    return tuple(c.text for c in changes if c.material)
