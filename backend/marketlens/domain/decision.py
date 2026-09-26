"""Final decision engine (deterministic, no network, no LLM).

Order of precedence:
1. Hard vetoes (cannot be overridden by anything, including the AI committee)
2. Score bands with hysteresis (separate enter/exit thresholds, per action level)
3. Price plan (buy zone / R/R — also required for ADD) and event-risk limits
4. Material-change gate (a recommendation only changes when something material changed)
5. Portfolio risk gate for EVERY bullish action (BUY, BUY SMALL, ADD)
6. Downgrade-only adjustments from the risk review (``apply_downgrade``)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from marketlens.domain.enums import BULLISH_ACTIONS, Action, DataQuality, HardVeto
from marketlens.domain.entry import EntryPlan
from marketlens.domain.facts import DataQualityReport
from marketlens.domain.scoring import ScoreCard


@dataclass(frozen=True, slots=True)
class DecisionThresholds:
    buy_enter: float = 80.0
    buy_exit: float = 76.0
    buy_small_enter: float = 72.0
    buy_small_exit: float = 68.0
    watch_floor: float = 55.0
    hold_floor: float = 55.0
    reduce_floor: float = 45.0
    min_completeness: float = 0.6
    min_liquidity_dollar_volume: float = 2e7
    material_score_delta: float = 5.0
    material_rr_delta: float = 0.5
    material_revision_delta: float = 0.02
    min_rr: float = 2.0


@dataclass(frozen=True, slots=True)
class DecisionContext:
    held: bool
    previous_action: Action | None
    price_quality: DataQuality
    data_quality: DataQualityReport
    severe_conflicts: tuple[str, ...]
    thesis_invalidated: bool
    thesis_breaches: tuple[str, ...]
    avg_dollar_volume: float | None
    event_risk_level: str  # LOW | MEDIUM | HIGH | EXTREME
    material_changes: tuple[str, ...]  # computed by what_changed.detect_material_changes
    portfolio_size_cap: str | None = None  # FULL/HALF/SMALL/WATCH from deterministic portfolio check
    stale_core: tuple[str, ...] = ()  # core inputs present but too old (fundamentals, price history)
    binary_event: bool = False  # EXTREME risk comes from a binary outcome (FDA/antitrust/regulatory)
    prior_stop_breached: bool = False  # a completed session CLOSED at/below the stop of the previous bullish recommendation
    intraday_stop_breach: bool = False  # the current quote is below that stop but no session has closed there yet
    sector_unknown: bool = False  # no reliable sector/industry → generic model, lower confidence, no full BUY
    model_coverage_gaps: tuple[str, ...] = ()  # core sector-model components that could not be scored (e.g. "fundamental")


@dataclass(frozen=True, slots=True)
class Decision:
    action: Action
    confidence: float  # 0..100
    vetoes: tuple[HardVeto, ...]
    reasons: tuple[str, ...]
    raw_action: Action  # before hysteresis / material-change gate
    suppressed_change: bool = False
    size_limit: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def evaluate_vetoes(ctx: DecisionContext, th: DecisionThresholds) -> list[HardVeto]:
    v: list[HardVeto] = []
    if ctx.price_quality in (DataQuality.STALE, DataQuality.MISSING, DataQuality.CONFLICTING):
        v.append(HardVeto.STALE_PRICE)
    if ctx.stale_core:
        v.append(HardVeto.STALE_CORE_DATA)
    if ctx.data_quality.core_missing or ctx.data_quality.completeness < th.min_completeness:
        v.append(HardVeto.MISSING_CORE_DATA)
    if ctx.severe_conflicts:
        v.append(HardVeto.SEVERE_DATA_CONFLICT)
    if ctx.thesis_invalidated:
        v.append(HardVeto.THESIS_INVALIDATED)
    if ctx.avg_dollar_volume is None or ctx.avg_dollar_volume < th.min_liquidity_dollar_volume:
        v.append(HardVeto.UNACCEPTABLE_LIQUIDITY)
    if ctx.event_risk_level == "EXTREME":
        v.append(HardVeto.EXTREME_EVENT_RISK)
    if ctx.model_coverage_gaps:
        v.append(HardVeto.INSUFFICIENT_MODEL_COVERAGE)
    return v


def _plan_ok(plan: EntryPlan | None, th: DecisionThresholds) -> bool:
    return plan is not None and plan.in_buy_zone and (plan.rr_at_current or 0) >= th.min_rr - 1e-9


def _raw_action(score: float, held: bool, plan: EntryPlan | None, prev: Action | None, th: DecisionThresholds, sell_score: float | None = None) -> tuple[Action, list[str]]:
    """``sell_score`` (missing inputs = neutral) decides REDUCE/SELL; ``score`` (missing = conservative) decides buys."""
    reasons: list[str] = []
    sell_score = score if sell_score is None else sell_score
    # hysteresis per level: a previous BUY/ADD keeps BUY until the BUY exit; a previous BUY SMALL keeps
    # BUY SMALL until its exit but must still reach the full BUY *enter* threshold to become BUY.
    buy_th = th.buy_exit if prev in (Action.BUY, Action.ADD) else th.buy_enter
    small_th = th.buy_small_exit if prev in BULLISH_ACTIONS else th.buy_small_enter
    if prev in BULLISH_ACTIONS:
        reasons.append(f"히스테리시스 적용: 매수 유지 기준 {buy_th:g}, 소량 매수 유지 기준 {small_th:g}")
    if held:
        if sell_score < th.reduce_floor:
            return Action.SELL, reasons + [f"매도 판단 점수(누락 데이터는 중립 처리) {sell_score} < {th.reduce_floor:g} → 매도"]
        if sell_score < th.hold_floor:
            return Action.REDUCE, reasons + [f"매도 판단 점수(누락 데이터는 중립 처리) {sell_score} < {th.hold_floor:g} → 비중 축소"]
        if score < th.hold_floor:
            reasons.append(f"점수 {score}는 낮지만 누락 데이터를 중립으로 보면 {sell_score} → 데이터 부족을 매도 근거로 쓰지 않음")
        if score >= buy_th and plan is not None and plan.add_zone_low <= plan.current_price <= plan.add_zone_high:
            if (plan.rr_at_current or 0) >= th.min_rr:
                return Action.ADD, reasons + [f"점수 {score} ≥ {buy_th:g}, 추가매수 구간 진입, 손익비 {plan.rr_at_current} ≥ {th.min_rr:g}"]
            return Action.HOLD, reasons + [f"추가매수 구간이지만 손익비 {plan.rr_at_current} < {th.min_rr:g} → 보유"]
        return Action.HOLD, reasons + [f"보유 중, 점수 {score}"]
    if score >= small_th and plan is None:
        return Action.WAIT, reasons + ["점수는 매력적이나 유효한 가격 계획이 없음 → 대기"]
    if score >= buy_th:
        if _plan_ok(plan, th):
            return Action.BUY, reasons + [f"점수 {score} ≥ {buy_th:g}, 현재가 {plan.current_price} ≤ 최대 매수가 {plan.max_buy}"]  # type: ignore[union-attr]
        return Action.WAIT, reasons + [f"점수 {score} ≥ {buy_th:g} 이지만 현재가가 최대 매수가 {plan.max_buy if plan else 'N/A'} 초과 → 대기"]
    if score >= small_th:
        if _plan_ok(plan, th):
            return Action.BUY_SMALL, reasons + [f"점수 {score} ≥ {small_th:g}, 매수 구간 안"]
        return Action.WAIT, reasons + [f"점수 {score} ≥ {small_th:g} 이지만 매수 구간 밖 → 대기"]
    if score >= th.watch_floor:
        return Action.WATCH, reasons + [f"점수 {score}: 관찰 구간"]
    return Action.WATCH, reasons + [f"점수 {score} < {th.watch_floor:g}: 매력 낮음"]


def _apply_vetoes(action: Action, vetoes: list[HardVeto], ctx: DecisionContext) -> tuple[Action, str | None, list[str]]:
    held = ctx.held
    notes: list[str] = []
    if {HardVeto.STALE_PRICE, HardVeto.STALE_CORE_DATA, HardVeto.MISSING_CORE_DATA, HardVeto.SEVERE_DATA_CONFLICT} & set(vetoes):
        notes.append("하드 거부권: 데이터가 오래되었거나 부족·충돌 → 어떤 행동도 권고하지 않음")
        return Action.DATA_INSUFFICIENT, None, notes
    if HardVeto.THESIS_INVALIDATED in vetoes:
        notes.append("하드 거부권: 투자 논리 훼손")
        return (Action.SELL if held else Action.WAIT), None, notes
    if HardVeto.INSUFFICIENT_MODEL_COVERAGE in vetoes:
        gaps = ", ".join(ctx.model_coverage_gaps)
        if held and ctx.prior_stop_breached:  # a price fact, not a data gap
            notes.append(f"업종 모델 데이터 부족({gaps})이지만 직전 추천의 손절가 이탈은 가격 사실 → 매도")
            return Action.SELL, None, notes
        notes.append(f"하드 거부권: 업종 모델의 핵심 데이터 부족({gaps}) → 매수·매도 판단을 하지 않음 (데이터 부족은 매도 근거가 아님)")
        return Action.DATA_INSUFFICIENT, None, notes
    if HardVeto.UNACCEPTABLE_LIQUIDITY in vetoes and action in BULLISH_ACTIONS:
        notes.append("하드 거부권: 유동성 기준 미달")
        return (Action.HOLD if held else Action.WAIT), None, notes
    if HardVeto.EXTREME_EVENT_RISK in vetoes and action in BULLISH_ACTIONS:
        if ctx.binary_event or action == Action.ADD:
            notes.append("하드 거부권: 임박한 극단적 이벤트(양자택일형 결과 또는 추가매수) → 이벤트 이후로 대기")
            return (Action.HOLD if held else Action.WAIT), "WATCH", notes
        notes.append("하드 거부권: 극단적 이벤트 위험 → 소량 매수로 제한")
        return Action.BUY_SMALL, "SMALL", notes
    return action, None, notes


def _portfolio_gate(action: Action, cap: str | None, held: bool) -> tuple[Action, str | None]:
    """Apply the deterministic portfolio cap to every bullish action (never only BUY)."""
    if cap is None or action not in BULLISH_ACTIONS:
        return action, None
    if cap == "WATCH":
        return (Action.HOLD if held else Action.WATCH), f"포트폴리오 한도(집중도/현금) 초과 → {'보유' if held else '관찰'}"
    if cap == "SMALL" and action == Action.BUY:
        return Action.BUY_SMALL, "포트폴리오 한도 → 소량 매수로 제한"
    return action, None


SECTOR_UNKNOWN_CONFIDENCE_PENALTY = 15.0


def compute_confidence(card: ScoreCard, action: Action, th: DecisionThresholds, dq: DataQualityReport) -> float:
    """Deterministic base confidence: data completeness × distance from the nearest threshold.

    This is NOT a calibrated probability of success; it only expresses how robust the classification is."""
    s = card.total
    bounds = [th.buy_enter, th.buy_small_enter, th.watch_floor, th.reduce_floor]
    margin = min(abs(s - b) for b in bounds)
    margin_term = min(1.0, margin / 10.0)
    base = 45 + 35 * card.completeness * dq.completeness + 20 * margin_term
    if action == Action.DATA_INSUFFICIENT:
        base = min(base, 30)
    return round(max(0.0, min(100.0, base)), 1)


def decide(card: ScoreCard, plan: EntryPlan | None, ctx: DecisionContext, th: DecisionThresholds | None = None) -> Decision:
    th = th or DecisionThresholds()
    vetoes = evaluate_vetoes(ctx, th)
    raw, reasons = _raw_action(card.total, ctx.held, plan, ctx.previous_action, th, card.sell_side_total)
    if ctx.prior_stop_breached and raw in BULLISH_ACTIONS | {Action.HOLD}:
        raw = Action.SELL if ctx.held else Action.WAIT
        reasons.append("종가 기준 이탈: 직전 추천의 손절 기준가 아래로 마감 → " + ("매도" if ctx.held else "대기"))
    elif ctx.intraday_stop_breach and raw in BULLISH_ACTIONS:
        raw = Action.HOLD if ctx.held else Action.WAIT
        reasons.append("장중 손절 기준가 하회(종가 확인 전) → 신규 매수 중단" + (", 보유분은 종가 기준으로 판단" if ctx.held else ""))
    action, size_limit, notes = _apply_vetoes(raw, vetoes, ctx)

    suppressed = False
    prev = ctx.previous_action
    if not vetoes and not ctx.prior_stop_breached and prev is not None and action != prev and not ctx.material_changes and prev != Action.DATA_INSUFFICIENT:
        # no material change → keep the previous recommendation (avoid flip-flopping)
        notes.append(f"{prev.value} → {action.value} 변경 보류: 중요한 변화 없음")
        action = prev
        suppressed = True

    gated, why = _portfolio_gate(action, ctx.portfolio_size_cap, ctx.held)
    if why:
        notes.append(why)
        action = gated
        size_limit = ctx.portfolio_size_cap

    if ctx.sector_unknown and action == Action.BUY:
        notes.append("업종 분류 불명확 → 업종별 모델을 확정할 수 없어 소량 매수로 제한")
        action, size_limit = Action.BUY_SMALL, "SMALL"

    conf = compute_confidence(card, action, th, ctx.data_quality)
    if ctx.sector_unknown:
        conf = round(max(0.0, conf - SECTOR_UNKNOWN_CONFIDENCE_PENALTY), 1)
        notes.append(f"업종 분류 불명확 → 신뢰도 −{SECTOR_UNKNOWN_CONFIDENCE_PENALTY:g}")
    return Decision(
        action=action,
        confidence=conf,
        vetoes=tuple(vetoes),
        reasons=tuple(reasons),
        raw_action=raw,
        suppressed_change=suppressed,
        size_limit=size_limit,
        notes=tuple(notes),
    )


# --- downgrade-only adjustments (used by the risk review and the AI committee) --------------------

ALLOWED_DOWNGRADES: dict[Action, frozenset[Action]] = {
    Action.BUY: frozenset({Action.BUY_SMALL, Action.WAIT, Action.WATCH}),
    Action.BUY_SMALL: frozenset({Action.WAIT, Action.WATCH}),
    Action.ADD: frozenset({Action.HOLD, Action.WAIT}),
    Action.HOLD: frozenset({Action.REDUCE}),
    Action.REDUCE: frozenset({Action.SELL}),
}


def apply_downgrade(current: Action, proposed: Action | None) -> tuple[Action, bool]:
    """Return the final action. Only downgrades listed in ALLOWED_DOWNGRADES are accepted.

    Any attempted upgrade (e.g. WAIT → BUY, DATA INSUFFICIENT → BUY) is rejected.
    """
    if proposed is None or proposed == current:
        return current, False
    if proposed in ALLOWED_DOWNGRADES.get(current, frozenset()):
        return proposed, True
    return current, False


def bounded_confidence(base: float, adjustment: float, max_abs: float = 10.0) -> float:
    adj = max(-max_abs, min(max_abs, adjustment))
    return round(max(0.0, min(100.0, base + adj)), 1)
