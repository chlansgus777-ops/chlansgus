"""The price-dependent part of a recommendation, re-judged on every quote (real-time upgrade, 2026-09-29).

A scan (fundamentals, valuation, estimates, scoring) is slow and changes slowly; what changes by the second is where
the price stands against the stored plan. This module keeps, per ticker, the plan of its newest recommendation (on
today's share basis) and — for every new price — says:

- ``zone``: BUY_ZONE (a buy call, price at or under the max buy, above the stop, reward/risk still met) ·
  ABOVE_MAX · RR_LOW · STOP_HIT · TARGET_HIT · HOLD_RANGE (a held name between stop and target) · NO_PLAN
- live reward/risk and the distance to max buy / stop / target, the move since the analysis
- ``valid_now``: a buy call that still holds at this price with a current quote (the same rule as
  ``domain.freshness._revalidate`` — one definition of "the plan still holds")

A zone change is an event (entering the buy zone, a stop hit on a held name, a target hit): it is recorded as an
alert and may ask for a background re-analysis of that one name. Nothing here calls a provider or writes a row.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from marketlens.domain.facts import execution_quality
from marketlens.domain.enums import ACTION_KO, BULLISH_ACTIONS, Action, TradingSession
from marketlens.domain.freshness import PlanCheck, RevalidationPolicy, _revalidate, quote_current, recommendation_freshness
from marketlens.domain.market_calendar import classify_session

BUY_ZONE, ABOVE_MAX, RR_LOW, STOP_HIT, TARGET_HIT, HOLD_RANGE, NO_PLAN = (
    "BUY_ZONE", "ABOVE_MAX", "RR_LOW", "STOP_HIT", "TARGET_HIT", "HOLD_RANGE", "NO_PLAN")
REANALYZE_MOVE = 0.05  # a move this large since the analysis asks for a fresh analysis of the name
ALERTS_KEPT = 200


@dataclass(frozen=True)
class LivePlan:
    ticker: str
    rec_id: int
    as_of: datetime
    action: str
    bullish: bool
    data_ok: bool
    rec_price: float | None
    max_buy: float | None
    stop: float | None
    target1: float | None
    ideal_entry: float | None
    min_rr: float
    held: bool = False
    held_cost: float | None = None
    held_qty: float | None = None
    watched: bool = False
    live_price: bool = False


@dataclass
class Alert:
    id: int
    at: datetime
    ticker: str
    kind: str  # BUY_ZONE | STOP_HIT | TARGET_HIT | LEFT_BUY_ZONE | BIG_MOVE | REANALYZED
    level: str  # positive | warning | danger | info
    text: str
    price: float | None = None
    rec_id: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "at": self.at.isoformat(), "ticker": self.ticker, "kind": self.kind, "level": self.level, "text": self.text,
                "price": self.price, "rec_id": self.rec_id} | self.extra


def _pct(a: float | None, b: float | None) -> float | None:
    return None if a is None or b in (None, 0) else a / b - 1  # type: ignore[operator]


def judge(p: LivePlan, price: float, quote_ts: datetime | None, now: datetime, policy: RevalidationPolicy | None = None) -> dict[str, Any]:
    """The live verdict of one plan at one price (pure: the tests call it directly)."""
    pol = policy or RevalidationPolicy()
    session = classify_session(now)
    age = (now - quote_ts).total_seconds() if quote_ts is not None else None
    # a quote can prove the plan only while it is current: within the quote age limit, or the final close once the
    # market is closed (the same rule as recommendation freshness)
    current = quote_current(quote_ts, now, timedelta(seconds=60) if p.live_price else pol.max_quote_age, allow_close=not p.live_price)
    rr = None
    if p.stop is not None and p.target1 is not None and price > p.stop:
        rr = (p.target1 - price) / (price - p.stop)
    plan = PlanCheck(p.rec_price, p.max_buy, p.stop, p.target1, p.min_rr, p.bullish)
    problems = _revalidate(plan, price, pol) if p.bullish else []
    validity = recommendation_freshness(p.as_of, "FRESH" if p.data_ok else "STALE", now,
                                       plan=plan, quote_price=price, quote_ts=quote_ts, policy=pol)
    if not validity.actionable:
        problems.append(validity.reason_ko)
    if p.stop is not None and price <= p.stop:
        zone = STOP_HIT
    elif p.target1 is not None and price >= p.target1:
        zone = TARGET_HIT
    elif p.bullish and p.max_buy is not None:
        zone = ABOVE_MAX if price > p.max_buy else (RR_LOW if rr is not None and rr < p.min_rr - 1e-9 else BUY_ZONE)
    elif p.held and p.stop is not None:
        zone = HOLD_RANGE
    else:
        zone = NO_PLAN
    move = _pct(price, p.rec_price)
    return {
        "rec_id": p.rec_id, "as_of": p.as_of.isoformat(), "action": p.action, "action_ko": ACTION_KO.get(Action(p.action), p.action) if p.action in Action._value2member_map_ else p.action,
        "bullish": p.bullish, "zone": zone, "rr_now": rr, "min_rr": p.min_rr,
        "max_buy": p.max_buy, "stop": p.stop, "target": p.target1, "ideal_entry": p.ideal_entry,
        "to_max_pct": _pct(p.max_buy, price), "to_stop_pct": _pct(p.stop, price), "to_target_pct": _pct(p.target1, price),
        "move_pct": move, "quote_current": current, "quote_age_s": age,
        # "buy now" needs everything at once: a buy call made on fresh data, the price inside the plan, a current quote
        "valid_now": bool(p.bullish and p.data_ok and current and zone == BUY_ZONE and not problems),
        "problems": problems, "held": p.held, "watched": p.watched,
        "pnl_pct": _pct(price, p.held_cost) if p.held else None,
        "pnl_abs": (price - p.held_cost) * p.held_qty if p.held and p.held_cost is not None and p.held_qty else None,
        "needs_reanalysis": bool(move is not None and abs(move) >= REANALYZE_MOVE) or zone in (STOP_HIT, TARGET_HIT),
    }


class LiveJudge:
    """Plans by ticker, the last zone seen, and the alert log. Thread-safe; cheap enough to run on every quote row."""

    def __init__(self, now: Callable[[], datetime] | None = None, policy: RevalidationPolicy | None = None) -> None:
        self._now = now or (lambda: datetime.now(tz=timezone.utc))
        self._policy = policy or RevalidationPolicy()
        self._lock = threading.Lock()
        self._plans: dict[str, LivePlan] = {}
        self._zone: dict[str, tuple[int, str]] = {}  # ticker → (rec_id, zone) last announced
        self._alerts: deque[Alert] = deque(maxlen=ALERTS_KEPT)
        self._next_id = 1
        self._alert_cv = threading.Condition(self._lock)
        self.on_reanalyze: Callable[[str, str], None] | None = None  # (ticker, reason) — set by the service
        self.loaded_at = 0.0

    # ------------------------------------------------------------------ plans
    def set_plans(self, plans: list[LivePlan]) -> None:
        with self._lock:
            self._plans = {p.ticker: p for p in plans}
            # the last zone of a name is kept across a new analysis of it: still beyond the stop after a re-analysis is
            # not a new event (it was announced once), while a real change under the new plan still is
            self._zone = {t: z for t, z in self._zone.items() if t in self._plans}
        self.loaded_at = time.monotonic()

    def plan(self, ticker: str) -> LivePlan | None:
        with self._lock:
            return self._plans.get(ticker.upper())

    def tickers(self) -> list[str]:
        with self._lock:
            return list(self._plans)

    # ------------------------------------------------------------------ per price
    def annotate(self, ticker: str, price: float | None, quote_ts: datetime | None) -> dict[str, Any] | None:
        p = self.plan(ticker)
        if p is None or price is None or price <= 0:
            return None
        return judge(p, price, quote_ts, self._now(), self._policy)

    def observe(self, ticker: str, price: float, quote_ts: datetime | None) -> None:
        """A new price for ``ticker``: record an alert when its zone changed in a way the owner must know."""
        p = self.plan(ticker)
        if p is None or price <= 0:
            return
        j = judge(p, price, quote_ts, self._now(), self._policy)
        if not j["quote_current"]:
            return  # a stale price announces nothing (it could be yesterday's)
        zone = j["zone"]
        with self._lock:
            prev = self._zone.get(p.ticker)
            self._zone[p.ticker] = (p.rec_id, zone)
        if prev is not None and prev[0] != p.rec_id and zone in (ABOVE_MAX, RR_LOW, NO_PLAN, HOLD_RANGE) and prev[1] == BUY_ZONE:
            return  # the plan itself changed (re-analysis): leaving the old buy zone is not a price event
        if prev is None:
            if zone in (STOP_HIT, TARGET_HIT) and p.held:  # already beyond a level when first seen: still worth saying once
                self._event(p, j, zone, price)
            return
        if prev[1] == zone:
            return
        self._event(p, j, zone, price, prev[1])

    def _event(self, p: LivePlan, j: dict[str, Any], zone: str, price: float, prev: str | None = None) -> None:
        t = p.ticker
        who = "보유 종목" if p.held else "관심 종목" if p.watched else "후보"
        if zone == BUY_ZONE and j["valid_now"]:
            self.add(t, "BUY_ZONE", "positive", f"{t} 매수 구간 진입 — ${price:,.2f} (최대 매수가 ${p.max_buy:,.2f} 이하, 손익비 {j['rr_now']:.2f})", price, p.rec_id)
        elif zone == STOP_HIT:
            lvl = "danger" if p.held else "warning"
            self.add(t, "STOP_HIT", lvl, f"{t} 손절 기준 도달 — ${price:,.2f} ≤ ${p.stop:,.2f}" + (" · 보유 중: 매도 검토" if p.held else f" ({who})"), price, p.rec_id)
        elif zone == TARGET_HIT:
            self.add(t, "TARGET_HIT", "positive" if p.held else "info", f"{t} 1차 목표가 도달 — ${price:,.2f} ≥ ${p.target1:,.2f}" + (" · 보유 중: 일부 차익 검토" if p.held else ""), price, p.rec_id)
        elif prev == BUY_ZONE and zone in (ABOVE_MAX, RR_LOW):
            why = f"최대 매수가 ${p.max_buy:,.2f} 초과" if zone == ABOVE_MAX else f"손익비 {j['rr_now']:.2f} < {p.min_rr:g}"
            self.add(t, "LEFT_BUY_ZONE", "info", f"{t} 매수 구간 이탈 — ${price:,.2f} ({why})", price, p.rec_id)
        if j["needs_reanalysis"] and self.on_reanalyze is not None:
            self.on_reanalyze(t, zone)

    # ------------------------------------------------------------------ alerts
    def add(self, ticker: str, kind: str, level: str, text: str, price: float | None = None, rec_id: int | None = None, **extra: Any) -> Alert:
        with self._lock:
            a = Alert(self._next_id, self._now(), ticker, kind, level, text, price, rec_id, extra)
            self._next_id += 1
            self._alerts.append(a)
            self._alert_cv.notify_all()
        return a

    def alerts(self, after: int = 0, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return [a.as_dict() for a in self._alerts if a.id > after][-limit:]

    @property
    def last_alert_id(self) -> int:
        with self._lock:
            return self._next_id - 1


def plans_from_rows(rows: list[Any], levels: Callable[[Any], dict[str, Any]], min_rr: float, held: dict[str, tuple[float, float]],
                    watched: set[str]) -> list[LivePlan]:
    """LivePlans from stored recommendation rows (newest per ticker first in ``rows``); ``levels`` = levels_now."""
    out: dict[str, LivePlan] = {}
    bullish = {a.value for a in BULLISH_ACTIONS}
    for r in rows:
        if r.ticker in out:
            continue
        lv = levels(r)
        h = held.get(r.ticker)
        out[r.ticker] = LivePlan(
            ticker=r.ticker, rec_id=r.id, as_of=r.as_of if r.as_of.tzinfo else r.as_of.replace(tzinfo=timezone.utc), action=r.final_action,
            bullish=r.final_action in bullish, data_ok=execution_quality((r.result or {}).get("data_quality"), r.data_quality) in ("FRESH", "DELAYED"), rec_price=lv.get("price"),
            max_buy=lv.get("max_buy"), stop=lv.get("stop"), target1=lv.get("target1"), ideal_entry=lv.get("ideal_entry"), min_rr=min_rr,
            held=h is not None, held_cost=h[0] if h else None, held_qty=h[1] if h else None, watched=r.ticker in watched)
    return list(out.values())


PLAN_REFRESH = timedelta(minutes=5)
