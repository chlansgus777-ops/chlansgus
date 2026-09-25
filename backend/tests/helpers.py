"""Builders for domain objects used across tests."""

from __future__ import annotations

from marketlens.domain.decision import DecisionContext
from marketlens.domain.entry import EntryPlan
from marketlens.domain.enums import Action, DataQuality
from marketlens.domain.facts import DataQualityReport
from marketlens.domain.scoring import COMPONENTS, ComponentScore, ScoreCard

DEFAULT_WEIGHTS = {"fundamental": 25, "valuation": 15, "earnings_revision": 15, "catalyst": 10, "macro": 10, "technical": 10, "risk": 10, "entry_rr": 15}


def card(total: float, available: bool = True) -> ScoreCard:
    comps = tuple(ComponentScore(n, DEFAULT_WEIGHTS[n], total / 100, available, ()) for n in COMPONENTS)
    return ScoreCard("T", "test", comps, "generic", "test")


def plan(price: float = 100.0, max_buy: float = 102.0, stop: float = 92.0, t1: float = 120.0, add=(95.0, 98.0)) -> EntryPlan:
    rr = (t1 - price) / (price - stop) if price > stop else None
    return EntryPlan(price, 97.0, 97.0, max_buy, max_buy, add[0], add[1], stop, t1, t1 * 1.1, rr, rr, stop / price - 1, t1 / price - 1, 96.0, t1, ())


def good_dq() -> DataQualityReport:
    return DataQualityReport(fields=tuple((f, DataQuality.FRESH) for f in ("price", "price_history", "fundamentals", "analyst", "earnings", "macro")))


def ctx(**kw) -> DecisionContext:
    base = dict(held=False, previous_action=None, price_quality=DataQuality.FRESH, data_quality=good_dq(), severe_conflicts=(),
                thesis_invalidated=False, thesis_breaches=(), avg_dollar_volume=5e8, event_risk_level="LOW", material_changes=("initial",),
                portfolio_size_cap=None)
    base.update(kw)
    return DecisionContext(**base)


__all__ = ["Action", "card", "plan", "ctx", "good_dq"]
