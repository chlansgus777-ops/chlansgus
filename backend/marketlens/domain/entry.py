"""Entry engine: price plan (ideal entry, max buy, add zone, stop, targets, R/R).

Maximum buy price is derived from the minimum acceptable reward/risk:
    (T1 − P) / (P − stop) ≥ min_rr   ⇔   P ≤ (T1 + min_rr · stop) / (1 + min_rr)
"""

from __future__ import annotations

from dataclasses import dataclass

from marketlens.domain.indicators import TechnicalSnapshot


@dataclass(frozen=True, slots=True)
class EntryConfig:
    min_rr: float = 2.0
    stop_atr_buffer: float = 0.5
    max_stop_atr: float = 3.0
    fallback_stop_atr: float = 2.0
    min_target_atr: float = 1.0
    fallback_target1_atr: float = 3.0
    fallback_target2_atr: float = 5.0
    ideal_entry_atr_above_support: float = 0.25
    add_zone_atr: float = 0.5


@dataclass(frozen=True, slots=True)
class EntryPlan:
    current_price: float
    ideal_entry: float
    acceptable_low: float
    acceptable_high: float  # == max_buy
    max_buy: float
    add_zone_low: float
    add_zone_high: float
    stop: float
    target1: float
    target2: float
    rr_at_current: float | None
    rr_at_ideal: float | None
    downside_pct: float
    upside_t1_pct: float
    support_used: float | None
    resistance_used: float | None
    rationale: tuple[str, ...]

    @property
    def in_buy_zone(self) -> bool:
        return self.current_price <= self.max_buy

    @property
    def stop_breached(self) -> bool:
        return self.current_price <= self.stop


def _rr(price: float, stop: float, target: float) -> float | None:
    risk = price - stop
    if risk <= 0:
        return None
    return (target - price) / risk


def build_entry_plan(
    price: float,
    tech: TechnicalSnapshot,
    cfg: EntryConfig | None = None,
    expected_move: float | None = None,
) -> EntryPlan | None:
    cfg = cfg or EntryConfig()
    a = tech.atr14
    if price is None or price <= 0 or a is None or a <= 0:
        return None
    notes: list[str] = []

    # --- structural support: nearest level below price within max_stop_atr ATRs
    # every support candidate must lie below the CURRENT price (swing levels were computed from the last
    # close, which differs from the current price after a gap)
    candidates: list[tuple[float, str]] = [(s, "스윙 저점") for s in tech.supports if s < price]
    for lvl, name in ((tech.sma50, "SMA50"), (tech.sma20, "SMA20"), (tech.anchored_vwap, "앵커드 VWAP"), (tech.sma200, "SMA200")):
        if lvl is not None and lvl < price:
            candidates.append((lvl, name))
    near = [(lvl, n) for lvl, n in candidates if price - lvl <= cfg.max_stop_atr * a]
    support: float | None = None
    if near:
        support, sname = max(near, key=lambda x: x[0])
        stop = support - cfg.stop_atr_buffer * a
        notes.append(f"손절가: {sname} {support:.2f} 아래 {cfg.stop_atr_buffer} ATR")
    else:
        stop = price - cfg.fallback_stop_atr * a
        notes.append(f"{cfg.max_stop_atr} ATR 이내 지지선 없음 → 변동성 손절 {cfg.fallback_stop_atr} ATR")

    # --- targets: resistances at least min_target_atr above price
    res = sorted(r for r in tech.resistances if r >= price + cfg.min_target_atr * a)
    if tech.high_52w is not None and tech.high_52w >= price + cfg.min_target_atr * a and tech.high_52w not in res:
        res = sorted(res + [tech.high_52w])
    resistance: float | None = None
    if res:
        resistance = res[0]
        t1 = res[0]
        notes.append(f"1차 목표가: 저항선 {t1:.2f}")
    else:
        t1 = price + cfg.fallback_target1_atr * a
        notes.append(f"상단 저항 없음 → 1차 목표가 = 현재가 + {cfg.fallback_target1_atr} ATR (신고가 구간)")
    if len(res) >= 2 and res[1] > t1:
        t2 = res[1]
    else:
        t2 = max(t1 + 1.5 * a, price + cfg.fallback_target2_atr * a)
    if expected_move is not None and expected_move > 0:
        notes.append(f"옵션 내재 예상변동폭 ±{expected_move:.1%} (목표가 현실성 참고)")

    max_buy = (t1 + cfg.min_rr * stop) / (1 + cfg.min_rr)
    ideal = (support + cfg.ideal_entry_atr_above_support * a) if support is not None else min(price, max_buy)
    ideal = min(ideal, max_buy)
    low = min(ideal, max_buy)
    add_low = support if support is not None else stop + 0.5 * a
    add_high = add_low + cfg.add_zone_atr * a
    notes.append(f"최대 매수가 {max_buy:.2f} = 최소 손익비 {cfg.min_rr} 에서 역산")
    # price-plan invariant: stop < current price < target1 < target2 (never publish an inconsistent plan)
    if not (stop < price < t1 < t2 and stop < max_buy < t1):
        return None
    return EntryPlan(
        current_price=price,
        ideal_entry=round(ideal, 2),
        acceptable_low=round(low, 2),
        acceptable_high=round(max_buy, 2),
        max_buy=round(max_buy, 2),
        add_zone_low=round(add_low, 2),
        add_zone_high=round(add_high, 2),
        stop=round(stop, 2),
        target1=round(t1, 2),
        target2=round(t2, 2),
        rr_at_current=round(r, 2) if (r := _rr(price, stop, t1)) is not None else None,
        rr_at_ideal=round(r2, 2) if (r2 := _rr(ideal, stop, t1)) is not None else None,
        downside_pct=round((stop / price - 1), 4),
        upside_t1_pct=round((t1 / price - 1), 4),
        support_used=support,
        resistance_used=resistance,
        rationale=tuple(notes),
    )


@dataclass(frozen=True, slots=True)
class ThesisCondition:
    condition_id: str
    description: str
    metric: str | None = None  # optional machine-checkable metric
    operator: str | None = None  # "<" or ">"
    threshold: float | None = None
    issue_category: str | None = None  # flagged when an issue of this category hits hard enough
    issue_threshold: float | None = None  # FUNDAMENTAL-horizon impact score (e.g. -40)


def evaluate_thesis(conditions: list[ThesisCondition], metrics: dict[str, float | None], flagged: set[str]) -> tuple[bool, list[str]]:
    """Thesis invalidation is separate from the price stop.

    A condition is breached if its metric crosses the threshold, or if an issue/analyst flagged it.
    """
    breached: list[str] = []
    for c in conditions:
        if c.condition_id in flagged:
            breached.append(c.description)
            continue
        if c.metric and c.operator and c.threshold is not None:
            v = metrics.get(c.metric)
            if v is None:
                continue
            if (c.operator == "<" and v < c.threshold) or (c.operator == ">" and v > c.threshold):
                breached.append(f"{c.description} ({c.metric}={v:.4g} {c.operator} {c.threshold})")
    return (len(breached) > 0, breached)
