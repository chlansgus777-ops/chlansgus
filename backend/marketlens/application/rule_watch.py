"""내 규칙 알림 (owner 2026-10-04): every live price of a held name is checked against the owner's saved trade rules
(손절 · 1차 익절 · 추적 손절 · 추가매수), and a change of what the rule says to do becomes an alert in the app's alert
center — once per change, never on every tick. MarketLens never places the order.

The holdings' rule state (average cost, first purchase, adds, partial sales, high since opening) is rebuilt from the
account in the background (``load``); a price only re-runs ``holding_plan`` on that state, so a tick costs microseconds.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Callable

from marketlens.domain.habit_diagnosis import HoldingState, TradeRules, holding_plan

log = logging.getLogger(__name__)

STALE_QUOTE = timedelta(minutes=20)  # an older price (yesterday's close after a restart) announces nothing
ACTED = ("STOP", "TRAIL", "TAKE1", "ADD")
LEVEL = {"STOP": "danger", "TRAIL": "warning", "TAKE1": "positive", "ADD": "info"}

Loader = Callable[[], tuple[list[HoldingState], TradeRules]]
AddAlert = Callable[..., Any]


class RuleWatch:
    def __init__(self, add: AddAlert, now: Callable[[], datetime]) -> None:
        self._add = add
        self._now = now
        self._lock = threading.Lock()
        self._states: dict[str, HoldingState] = {}
        self._rules = TradeRules()
        self._last: dict[str, str] = {}  # symbol -> the action last announced (or seen)
        self.loader: Loader | None = None

    def load(self) -> int:
        """Rebuild the holdings' rule state from the account (background; a failure keeps the previous state)."""
        if self.loader is None:
            return 0
        try:
            states, rules = self.loader()
        except Exception as e:  # noqa: BLE001 - alerts are a convenience; the screens still show the plan
            log.warning("rule watch: load failed: %s", type(e).__name__)
            return len(self._states)
        with self._lock:
            # the highest price seen live survives a reload (the daily bars may not have today's high yet)
            old = self._states
            self._states = {}
            for st in states:
                prev = old.get(st.symbol)
                hi = st.high_since_open
                if prev is not None and prev.high_since_open is not None and st.opened == prev.opened:
                    hi = max(hi or prev.high_since_open, prev.high_since_open)
                self._states[st.symbol] = replace(st, high_since_open=hi)
            if rules != self._rules:
                self._last = {}  # new rules: what they say now is news again
            self._rules = rules
            self._last = {k: v for k, v in self._last.items() if k in self._states}
        return len(self._states)

    def symbols(self) -> list[str]:
        with self._lock:
            return list(self._states)

    def observe(self, symbol: str, price: float, ts: datetime | None) -> dict[str, Any] | None:
        """A live price of ``symbol``: the rule's action at that price; an alert when it changed to an action."""
        sym = symbol.upper()
        if price is None or price <= 0 or ts is None or self._now() - ts > STALE_QUOTE:
            return None
        with self._lock:
            st = self._states.get(sym)
            if st is None:
                return None
            hi = st.high_since_open
            if hi is not None and price > hi:
                st = replace(st, high_since_open=price)
                self._states[sym] = st
            rules = self._rules
            plan = holding_plan(replace(st, price=price, price_at=ts.isoformat(), price_source="실시간"), rules)
            act = plan["action"]
            prev = self._last.get(sym)
            self._last[sym] = act
        if act == prev or act not in ACTED:
            return plan
        why = "저장한 내 규칙" if rules.saved_at else "기본 규칙(아직 저장 전)"
        self._add(sym, f"RULE_{act}", LEVEL[act], f"{sym} {plan['action_ko']} — {plan['detail']} · {why} · 주문은 직접",
                  price, None, source="rule", action=act)
        return plan
