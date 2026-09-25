"""Final decision engine (deterministic, no network, no LLM).

Order of precedence:
1. Hard vetoes (cannot be overridden by anything, including the AI committee)
2. Score bands with hysteresis (separate enter/exit thresholds)
3. Price plan (buy zone / R/R) and event-risk sizing limits
4. Material-change gate (a recommendation only changes when something material changed)
5. Downgrade-only adjustments from the risk review (``apply_downgrade``)
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
    return v


def _raw_action(score: float, held: bool, plan: EntryPlan | None, prev: Action | None, th: DecisionThresholds) -> tuple[Action, list[str]]:
    reasons: list[str] = []
    was_bullish = prev in BULLISH_ACTIONS
    buy_th = th.buy_exit if was_bullish else th.buy_enter
    small_th = th.buy_small_exit if was_bullish else th.buy_small_enter
    if was_bullish:
        reasons.append(f"hysteresis: using exit thresholds (BUY ≥ {th.buy_exit}, BUY SMALL ≥ {th.buy_small_exit})")
    in_zone = plan is not None and plan.in_buy_zone and (plan.rr_at_current or 0) > 0
    if held:
        if score < th.reduce_floor:
            return Action.SELL, reasons + [f"score {score} < {th.reduce_floor}"]
        if score < th.hold_floor:
            return Action.REDUCE, reasons + [f"score {score} < {th.hold_floor}"]
        if score >= buy_th and plan is not None and plan.add_zone_low <= plan.current_price <= plan.add_zone_high:
            return Action.ADD, reasons + [f"score {score} ≥ {buy_th} and price inside add zone"]
        return Action.HOLD, reasons + [f"held; score {score}"]
    if score >= small_th and plan is None:
        return Action.WAIT, reasons + ["attractive score but no valid price plan"]
    if score >= buy_th:
        if in_zone:
            return Action.BUY, reasons + [f"score {score} ≥ {buy_th}; price {plan.current_price} ≤ max buy {plan.max_buy}"]  # type: ignore[union-attr]
        return Action.WAIT, reasons + [f"score {score} ≥ {buy_th} but price above max buy {plan.max_buy if plan else 'N/A'}"]
    if score >= small_th:
        if in_zone:
            return Action.BUY_SMALL, reasons + [f"score {score} ≥ {small_th}; price in buy zone"]
        return Action.WAIT, reasons + [f"score {score} ≥ {small_th} but price above max buy"]
    if score >= th.watch_floor:
        return Action.WATCH, reasons + [f"score {score} in watch band"]
    return Action.WATCH, reasons + [f"score {score} below watch floor {th.watch_floor}: not attractive"]


def _apply_vetoes(action: Action, vetoes: list[HardVeto], held: bool) -> tuple[Action, str | None, list[str]]:
    notes: list[str] = []
    size_limit: str | None = None
    if HardVeto.STALE_PRICE in vetoes or HardVeto.MISSING_CORE_DATA in vetoes or HardVeto.SEVERE_DATA_CONFLICT in vetoes:
        notes.append("hard veto: data not reliable enough for any action")
        return Action.DATA_INSUFFICIENT, None, notes
    if HardVeto.THESIS_INVALIDATED in vetoes:
        notes.append("hard veto: thesis invalidated")
        return (Action.SELL if held else Action.WAIT), None, notes
    if HardVeto.UNACCEPTABLE_LIQUIDITY in vetoes and action in BULLISH_ACTIONS:
        notes.append("hard veto: liquidity below minimum")
        return (Action.HOLD if held else Action.WAIT), None, notes
    if HardVeto.EXTREME_EVENT_RISK in vetoes:
        size_limit = "SMALL"
        if action == Action.BUY:
            notes.append("hard veto: extreme event risk → BUY capped to BUY SMALL")
            return Action.BUY_SMALL, size_limit, notes
        if action == Action.ADD:
            notes.append("hard veto: extreme event risk → no adds before the event")
            return Action.HOLD, size_limit, notes
    return action, size_limit, notes


def compute_confidence(card: ScoreCard, action: Action, th: DecisionThresholds, dq: DataQualityReport) -> float:
    """Deterministic base confidence: data completeness × distance from the nearest threshold."""
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
    raw, reasons = _raw_action(card.total, ctx.held, plan, ctx.previous_action, th)
    action, size_limit, notes = _apply_vetoes(raw, vetoes, ctx.held)

    suppressed = False
    prev = ctx.previous_action
    if not vetoes and prev is not None and action != prev and not ctx.material_changes and prev != Action.DATA_INSUFFICIENT:
        # no material change → keep the previous recommendation (avoid flip-flopping)
        notes.append(f"change {prev.value} → {action.value} suppressed: no material change")
        action = prev
        suppressed = True

    if ctx.portfolio_size_cap in ("SMALL", "WATCH") and action == Action.BUY:
        action = Action.BUY_SMALL if ctx.portfolio_size_cap == "SMALL" else Action.WATCH
        notes.append(f"portfolio concentration cap {ctx.portfolio_size_cap}")
        size_limit = ctx.portfolio_size_cap

    conf = compute_confidence(card, action, th, ctx.data_quality)
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
