"""Deterministic scoring.

FACTS → SCORE → DECISION. This module must never import the decision engine, and ``ScoringInputs``
deliberately has no field for a previous or proposed action (enforced by tests).

Partial data is never rewarded: each component reports the share of its inputs that were available
(coverage). The published sub-score blends the measured value with the conservative
``missing_component_subscore`` in proportion to the missing share, and a component with coverage below
``MIN_COMPONENT_COVERAGE`` counts as unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from marketlens.domain.catalysts import EventRisk
from marketlens.domain.earnings import QUALITY_SCORE, RESULT_KO, EarningsAssessment, ExpectationBar, RevisionAssessment
from marketlens.domain.entry import EntryPlan
from marketlens.domain.indicators import TechnicalSnapshot
from marketlens.domain.macro import MacroImpact
from marketlens.domain.sector_models import RuleScore
from marketlens.domain.valuation import RelativeValuation

COMPONENTS = ("fundamental", "valuation", "earnings_revision", "catalyst", "macro", "technical", "risk", "entry_rr")
COMPONENT_KO = {
    "fundamental": "펀더멘털", "valuation": "밸류에이션", "earnings_revision": "실적·추정치", "catalyst": "촉매·이슈",
    "macro": "거시", "technical": "기술적 위치", "risk": "위험", "entry_rr": "진입 손익비",
}
MIN_COMPONENT_COVERAGE = 0.5


@dataclass(frozen=True, slots=True)
class ScoringModel:
    version: str
    weights: Mapping[str, float]
    missing_component_subscore: float = 0.35

    def __post_init__(self) -> None:
        if set(self.weights) != set(COMPONENTS):
            raise ValueError(f"weights must define exactly {COMPONENTS}")
        if any(w < 0 for w in self.weights.values()):
            raise ValueError("weights must be non-negative")

    @property
    def total_weight(self) -> float:
        return float(sum(self.weights.values()))


@dataclass(frozen=True, slots=True)
class Reason:
    text: str
    sign: int  # +1 supportive, -1 detracting, 0 neutral
    refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ComponentScore:
    name: str
    weight: float
    subscore: float  # 0..1 (already blended with the conservative default for missing inputs)
    available: bool
    reasons: tuple[Reason, ...]
    missing: tuple[str, ...] = ()
    coverage: float = 1.0

    @property
    def points(self) -> float:
        return round(self.subscore * self.weight, 2)


@dataclass(frozen=True, slots=True)
class ScoreCard:
    ticker: str
    model_version: str
    components: tuple[ComponentScore, ...]
    sector_model_id: str
    sector_model_reason: str

    @property
    def total(self) -> float:
        tw = sum(c.weight for c in self.components)
        if tw <= 0:
            return 0.0
        return round(sum(c.subscore * c.weight for c in self.components) / tw * 100, 2)

    @property
    def completeness(self) -> float:
        """Weight-averaged input coverage across components (0..1)."""
        tw = sum(c.weight for c in self.components)
        return round(sum(c.weight * (c.coverage if c.available else 0.0) for c in self.components) / tw, 4) if tw else 0.0

    def component(self, name: str) -> ComponentScore:
        return next(c for c in self.components if c.name == name)

    def top_factors(self, n: int = 3) -> tuple[list[Reason], list[Reason]]:
        pos = [r for c in self.components for r in c.reasons if r.sign > 0]
        neg = [r for c in self.components for r in c.reasons if r.sign < 0]
        return pos[:n], neg[:n]


@dataclass(frozen=True, slots=True)
class ScoringInputs:
    ticker: str
    sector_model_id: str
    sector_model_reason: str
    fundamental: RuleScore | None
    valuation_absolute: RuleScore | None
    relative_valuation: RelativeValuation | None
    revisions: RevisionAssessment | None
    earnings: EarningsAssessment | None
    issue_score_swing: float | None  # -100..100 aggregated
    upcoming_catalyst_bias: float | None  # -1..1 (positive setup into a catalyst)
    macro: MacroImpact | None
    risk_off_active: bool
    beta: float | None
    technicals: TechnicalSnapshot | None
    entry: EntryPlan | None
    event_risk: EventRisk | None
    net_debt_to_ebitda: float | None
    avg_dollar_volume: float | None
    short_interest_pct: float | None
    data_completeness: float
    reference_facts: Mapping[str, float | None] = field(default_factory=dict)


def lin(x: float, bad: float, good: float) -> float:
    if good == bad:
        return 1.0 if x >= good else 0.0
    return max(0.0, min(1.0, (x - bad) / (good - bad)))


def _wavg(parts: list[tuple[float | None, float]]) -> tuple[float | None, float]:
    avail = [(v, w) for v, w in parts if v is not None]
    tw = sum(w for _, w in parts)
    aw = sum(w for _, w in avail)
    if not avail or aw == 0:
        return None, 0.0
    return sum(v * w for v, w in avail) / aw, aw / tw


def _pct(x: float | None) -> str:
    return "N/A" if x is None else f"{x * 100:+.1f}%"


_PCT_HINTS = ("growth", "margin", "yield", "roe", "roic", "rotce", "cet1", "nim", "rate", "share", "dilution", "change", "payout", "occupancy", "nrr", "discount", "to_loans", "to_revenue", "vs_revenue")


def fmt_metric(metric: str, value: float) -> str:
    """Human-readable metric value for explanations (percent / multiple / plain)."""
    if metric == "rule_of_40":
        return f"{value:.0f}"
    if metric.endswith("_to_ebitda") or "coverage" in metric or metric in ("book_to_bill",):
        return f"{value:.2f}배"
    if any(h in metric for h in _PCT_HINTS):
        return f"{value * 100:.1f}%"
    return f"{value:.3g}"


Calc = tuple[float | None, float, list[Reason], list[str]]  # (sub, coverage, reasons, missing)


def _fundamental(inp: ScoringInputs) -> Calc:
    rs = inp.fundamental
    if rs is None or rs.subscore is None:
        return None, rs.coverage if rs else 0.0, [Reason("업종 모델에 필요한 재무 데이터 부족", -1)], list(rs.missing) if rs else ["fundamentals"]
    reasons: list[Reason] = []
    ranked = sorted((i for i in rs.items if i.subscore is not None), key=lambda i: -(i.subscore or 0) * i.weight)
    for i in ranked[:3]:
        if (i.subscore or 0) >= 0.6:
            reasons.append(Reason(f"{i.label} {fmt_metric(i.metric, i.value)} (업종 기준 우수)", +1, (f"fund.{i.metric}",)))  # type: ignore[arg-type]
    for i in sorted((i for i in rs.items if i.subscore is not None), key=lambda i: (i.subscore or 0))[:2]:
        if (i.subscore or 0) <= 0.35:
            reasons.append(Reason(f"{i.label} {fmt_metric(i.metric, i.value)} (업종 기준 취약)", -1, (f"fund.{i.metric}",)))  # type: ignore[arg-type]
    return rs.subscore, rs.coverage, reasons, list(rs.missing)


def _valuation(inp: ScoringInputs) -> Calc:
    reasons: list[Reason] = []
    missing: list[str] = []
    absolute = inp.valuation_absolute.subscore if inp.valuation_absolute else None
    rv = inp.relative_valuation
    hist = (1 - rv.history_percentile) if rv and rv.history_percentile is not None else None
    peer = lin(rv.premium_to_peers, 0.5, -0.3) if rv and rv.premium_to_peers is not None else None
    rate = lin(rv.equity_risk_spread, -0.03, 0.03) if rv and rv.equity_risk_spread is not None else None
    for name, v in (("absolute", absolute), ("history", hist), ("peers", peer), ("rates", rate)):
        if v is None:
            missing.append(f"valuation.{name}")
    sub, cov = _wavg([(absolute, 0.45), (hist, 0.2), (peer, 0.15), (rate, 0.2)])
    if rv is not None:
        if rv.primary_value is not None:
            reasons.append(Reason(f"{rv.primary_multiple} {rv.primary_value:.1f}", 0, (f"val.{rv.primary_multiple}",)))
        if hist is not None:
            reasons.append(Reason(f"{rv.primary_multiple} 자체 과거 대비 {rv.history_percentile:.0%} 분위", +1 if hist >= 0.5 else -1, ("val.history",)))
        if rv.premium_to_peers is not None:
            reasons.append(Reason(f"동종업계 중앙값 대비 {_pct(rv.premium_to_peers)}", +1 if rv.premium_to_peers < 0 else -1, ("val.peers",)))
        if rv.equity_risk_spread is not None:
            reasons.append(Reason(f"선행 이익수익률 − 미 10년물 = {_pct(rv.equity_risk_spread)}", +1 if rv.equity_risk_spread > 0 else -1, ("val.rate_spread", "macro.US10Y")))
        if rv.growth_adjusted is not None:
            reasons.append(Reason(f"PEG {rv.growth_adjusted:.2f}", +1 if rv.growth_adjusted < 1.5 else -1, ("val.peg",)))
    return sub, cov, reasons, missing


def _earnings(inp: ScoringInputs) -> Calc:
    reasons: list[Reason] = []
    missing: list[str] = []
    rev = inp.revisions
    er = inp.earnings
    breadth = rev.breadth_score if rev else None
    rev_dir = (0.5 + 0.5 * rev.revenue_direction) if rev else None
    quality = QUALITY_SCORE.get(er.result_quality) if er else None
    bar = None
    if er is not None and er.expectation_bar != ExpectationBar.UNKNOWN:
        bar = {ExpectationBar.HIGH: 0.3, ExpectationBar.NORMAL: 0.6, ExpectationBar.LOW: 0.8}[er.expectation_bar]
    if rev is None:
        missing.append("analyst_revisions")
    if er is None:
        missing.append("earnings_history")
    sub, cov = _wavg([(breadth, 0.45), (rev_dir, 0.15), (quality, 0.3), (bar, 0.1)])
    if rev is not None:
        word = {1: "상향", 0: "변화 없음", -1: "하향"}
        reasons.append(Reason(f"EPS 추정치 {word[rev.eps_direction]}", rev.eps_direction, ("analyst.eps_revision_30d", "analyst.eps_revision_90d")))
        reasons.append(Reason(f"매출 추정치 {word[rev.revenue_direction]}", rev.revenue_direction, ("analyst.revenue_revision_30d",)))
        if rev.low_coverage:
            reasons.append(Reason("애널리스트 커버리지 적음", -1, ("analyst.analyst_count",)))
        if rev.high_dispersion:
            reasons.append(Reason("추정치 편차 큼", -1, ("analyst.estimate_dispersion",)))
    if er is not None:
        sign = 1 if (quality or 0) >= 0.6 else -1 if (quality or 0) <= 0.4 else 0
        reasons.append(Reason(f"직전 실적: {RESULT_KO[er.result_quality]} (매출 서프라이즈 {_pct(er.revenue_surprise)}, EPS 서프라이즈 {_pct(er.eps_surprise)})", sign, ("earnings.last",)))
        if er.expectation_bar == ExpectationBar.HIGH:
            reasons.append(Reason("다음 실적에 대한 시장 기대치가 높음", -1, ("earnings.expectation_bar",)))
    return sub, cov, reasons, missing


def _catalyst(inp: ScoringInputs) -> Calc:
    reasons: list[Reason] = []
    issue = (0.5 + inp.issue_score_swing / 200) if inp.issue_score_swing is not None else None
    cat = (0.5 + 0.5 * inp.upcoming_catalyst_bias) if inp.upcoming_catalyst_bias is not None else None
    sub, cov = _wavg([(issue, 0.6), (cat, 0.4)])
    if inp.issue_score_swing is not None and abs(inp.issue_score_swing) >= 5:
        reasons.append(Reason(f"이슈 순영향(2~6주) {inp.issue_score_swing:+.0f}", 1 if inp.issue_score_swing > 0 else -1, ("issues.net_swing",)))
    if inp.upcoming_catalyst_bias is not None and inp.upcoming_catalyst_bias != 0:
        reasons.append(Reason("다가오는 촉매 전 우호적 흐름" if inp.upcoming_catalyst_bias > 0 else "다가오는 촉매 전 부정적 흐름", 1 if inp.upcoming_catalyst_bias > 0 else -1, ("calendar.next",)))
    missing = [m for m, v in (("issues", issue), ("calendar", cat)) if v is None]
    return sub, cov, reasons, missing


def _macro(inp: ScoringInputs) -> Calc:
    if inp.macro is None:
        return None, 0.0, [], ["macro"]
    sub = 0.5 + 0.5 * inp.macro.net
    reasons = [Reason(expl, 1 if c > 0 else -1, (f"macro.{f}",)) for f, c, expl in inp.macro.contributions if abs(c) >= 0.05]
    if inp.risk_off_active and (inp.beta or 1.0) > 1.2:
        sub -= 0.1
        reasons.append(Reason(f"위험회피 국면이며 베타 {inp.beta:.2f} > 1.2", -1, ("macro.regime",)))
    return max(0.0, min(1.0, sub)), 1.0, reasons, []


def _technical(inp: ScoringInputs) -> Calc:
    t = inp.technicals
    if t is None or t.last_close is None:
        return None, 0.0, [], ["technicals"]
    reasons: list[Reason] = []
    trend = None
    if t.sma50 is not None and t.sma200 is not None:
        if t.last_close > t.sma50 > t.sma200:
            trend = 1.0
            reasons.append(Reason("상승 추세: 주가 > 50일선 > 200일선", 1, ("tech.sma50", "tech.sma200")))
        elif t.last_close > t.sma200:
            trend = 0.6
        else:
            trend = 0.2
            reasons.append(Reason("200일선 아래 (추세 약함)", -1, ("tech.sma200",)))
    rs = lin(t.rs_6m, -0.2, 0.2) if t.rs_6m is not None else None
    if t.rs_6m is not None and abs(t.rs_6m) > 0.1:
        reasons.append(Reason(f"6개월 상대강도(SPY 대비) {_pct(t.rs_6m)}", 1 if t.rs_6m > 0 else -1, ("tech.rs_6m",)))
    rsi_s = None
    if t.rsi14 is not None:
        if t.rsi14 > 75:
            rsi_s = 0.3
            reasons.append(Reason(f"RSI {t.rsi14:.0f}: 과열 구간 진입", -1, ("tech.rsi14",)))
        elif t.rsi14 < 30:
            rsi_s = 0.4
        elif 40 <= t.rsi14 <= 65:
            rsi_s = 1.0
        else:
            rsi_s = 0.7
    vol = lin(t.volume_ratio, 0.6, 1.5) if t.volume_ratio is not None else None
    sub, cov = _wavg([(trend, 0.4), (rs, 0.3), (rsi_s, 0.15), (vol, 0.15)])
    return sub, cov, reasons, []


EVENT_RISK_SCORE = {"LOW": 1.0, "MEDIUM": 0.6, "HIGH": 0.3, "EXTREME": 0.0}


def _risk(inp: ScoringInputs) -> Calc:
    reasons: list[Reason] = []
    t = inp.technicals
    vol = lin(t.volatility_20d, 0.6, 0.2) if t is not None and t.volatility_20d is not None else None
    lev = lin(inp.net_debt_to_ebitda, 4.0, 0.0) if inp.net_debt_to_ebitda is not None else None
    liq = lin(inp.avg_dollar_volume, 2e7, 5e8) if inp.avg_dollar_volume is not None else None
    ev = EVENT_RISK_SCORE[inp.event_risk.level] if inp.event_risk is not None else None
    si = lin(inp.short_interest_pct, 0.20, 0.02) if inp.short_interest_pct is not None else None
    dq = inp.data_completeness
    sub, cov = _wavg([(vol, 0.3), (lev, 0.2), (liq, 0.15), (ev, 0.2), (si, 0.1), (dq, 0.05)])
    if vol is not None and vol < 0.3 and t is not None and t.volatility_20d is not None:
        reasons.append(Reason(f"실현 변동성 높음 {t.volatility_20d:.0%}", -1, ("tech.volatility_20d",)))
    if lev is not None and lev < 0.3:
        reasons.append(Reason(f"레버리지: 순부채/EBITDA {inp.net_debt_to_ebitda:.1f}배", -1, ("fund.net_debt_to_ebitda",)))
    if inp.event_risk is not None and inp.event_risk.level in ("HIGH", "EXTREME"):
        reasons.append(Reason(f"이벤트 위험 {inp.event_risk.level}: {'; '.join(inp.event_risk.reasons)}", -1, ("calendar.event_risk",)))
    if si is not None and si < 0.4:
        reasons.append(Reason(f"공매도 잔고 유통주식 대비 {inp.short_interest_pct:.0%}", -1, ("ownership.short_interest",)))
    missing = [m for m, v in (("volatility", vol), ("leverage", lev), ("liquidity", liq), ("event_risk", ev), ("short_interest", si)) if v is None]
    return sub, cov, reasons, missing


def _entry(inp: ScoringInputs) -> Calc:
    e = inp.entry
    if e is None:
        return None, 0.0, [Reason("유효한 가격 계획 없음 (가격/ATR 부족 또는 계획 불일치)", -1)], ["entry_plan"]
    rr = lin(e.rr_at_current, 0.5, 3.0) if e.rr_at_current is not None else 0.0
    atr = inp.technicals.atr14 if inp.technicals and inp.technicals.atr14 else None
    if e.in_buy_zone:
        zone = 1.0
    elif atr:
        zone = max(0.0, 1 - (e.current_price - e.max_buy) / (2 * atr))
    else:
        zone = 0.0
    sub = 0.7 * rr + 0.3 * zone
    reasons = [
        Reason(f"현재가 기준 손익비 {e.rr_at_current if e.rr_at_current is not None else 'N/A'} (손절 {e.stop}, 1차 목표 {e.target1})", 1 if (e.rr_at_current or 0) >= 2 else -1, ("entry.rr",)),
        Reason("가격이 매수 구간 안" if e.in_buy_zone else f"가격이 최대 매수가 {e.max_buy} 초과", 1 if e.in_buy_zone else -1, ("entry.max_buy",)),
    ]
    return sub, 1.0, reasons, []


_CALC = {
    "fundamental": _fundamental,
    "valuation": _valuation,
    "earnings_revision": _earnings,
    "catalyst": _catalyst,
    "macro": _macro,
    "technical": _technical,
    "risk": _risk,
    "entry_rr": _entry,
}


def score(inp: ScoringInputs, model: ScoringModel) -> ScoreCard:
    comps: list[ComponentScore] = []
    d = model.missing_component_subscore
    for name in COMPONENTS:
        sub, cov, reasons, missing = _CALC[name](inp)
        available = sub is not None and cov >= MIN_COMPONENT_COVERAGE
        if available:
            # missing inputs pull the component toward the conservative default instead of being ignored
            blended = sub * cov + d * (1 - cov)  # type: ignore[operator]
        else:
            blended = d
        comps.append(
            ComponentScore(
                name=name,
                weight=float(model.weights[name]),
                subscore=round(blended, 4),
                available=available,
                reasons=tuple(reasons),
                missing=tuple(missing),
                coverage=round(cov, 4),
            )
        )
    return ScoreCard(inp.ticker, model.version, tuple(comps), inp.sector_model_id, inp.sector_model_reason)
