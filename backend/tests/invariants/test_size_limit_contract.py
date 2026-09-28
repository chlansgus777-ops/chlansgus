"""Invariant: one size limit, from the decision to the buy amount, the AI committee and the paper position
(independent review 2026-09-28, F01/F05). For every buy action × every limit: no amount above the limit, no positive
quantity under WATCH, no buy action left standing after a WATCH, and the paper position sized like the screen."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from marketlens.api.routes import _position_plan
from marketlens.application.committee.orchestrator import apply_committee
from marketlens.application.committee.schemas import PortfolioAdvice
from marketlens.domain.decision import decide
from marketlens.domain.enums import BULLISH_ACTIONS, Action
from marketlens.domain.paper import PaperConfig, position_notional
from marketlens.domain.portfolio import PortfolioLimits, position_plan
from marketlens.domain.sizing import effective_size, tightest
from tests.helpers import card, ctx, plan

NAV, PRICE, STOP = 100_000.0, 96.5, 92.0
LIM = PortfolioLimits()
WEIGHT = {"FULL": LIM.full_position, "HALF": LIM.half_position, "SMALL": LIM.small_position, "WATCH": 0.0}
CAPS = ("FULL", "HALF", "SMALL", "WATCH")


def _decision(action: Action, cap: str):
    if action == Action.ADD:  # held, inside the add zone with enough reward/risk
        return decide(card(90), plan(price=PRICE), ctx(held=True, portfolio_size_cap=cap))
    return decide(card(90 if action == Action.BUY else 74), plan(price=PRICE), ctx(portfolio_size_cap=cap))


@pytest.mark.parametrize("cap", CAPS)
@pytest.mark.parametrize("action", [Action.BUY, Action.BUY_SMALL, Action.ADD])
def test_the_portfolio_limit_reaches_the_amount_for_every_buy(action, cap):
    unlimited = _decision(action, None)
    assert unlimited.action == action  # the fixture really produces this action without a limit
    d = _decision(action, cap)
    if cap == "WATCH":
        assert d.action not in BULLISH_ACTIONS and position_plan(d.action.value, d.size_limit, NAV, PRICE, STOP) is None
        return
    p = position_plan(d.action.value, d.size_limit, NAV, PRICE, STOP)
    assert p is not None and p.amount <= WEIGHT[cap] * NAV + 1e-6, (action, cap, d.size_limit, p)
    assert p.amount <= WEIGHT[effective_size(action.value)] * NAV + 1e-6  # never above the action's own size either


@pytest.mark.parametrize("advice", CAPS)
@pytest.mark.parametrize("action", [Action.BUY, Action.BUY_SMALL, Action.ADD])
def test_the_portfolio_managers_limit_holds_for_every_buy(action, advice):
    pm = PortfolioAdvice(portfolio_fit="NEUTRAL", suggested_size=advice, summary="size advice")
    final, _ = apply_committee(action, None, pm, "FULL")
    if advice == "WATCH":
        assert final == (Action.HOLD if action == Action.ADD else Action.WATCH)  # no new money; a holding is kept
        return
    assert final in BULLISH_ACTIONS
    p = position_plan(final.value, tightest("FULL", advice), NAV, PRICE, STOP)
    assert p is not None and p.amount <= WEIGHT[advice] * NAV + 1e-6


@pytest.mark.parametrize("cap", CAPS)
@pytest.mark.parametrize("action", ["BUY", "BUY SMALL", "ADD"])
def test_the_paper_position_is_sized_like_the_screen(action, cap):
    cfg = PaperConfig()
    screen = position_plan(action, cap, NAV, PRICE, STOP, limits=LIM)
    paper = position_notional(action, cfg, cap)
    if cap == "WATCH":
        assert screen is None and paper == 0.0
        return
    assert screen is not None
    # the same fraction of a full position on both: the paper account and the screen never disagree on the size
    assert paper / cfg.position_notional == pytest.approx(screen.weight / LIM.full_position)


@pytest.mark.parametrize("stored", [("HALF", None), (None, "HALF"), ("SMALL", "HALF"), ("HALF", "SMALL")])
def test_the_buy_amount_reads_every_stored_limit(stored):
    """A row keeps the decision's limit in result.decision.size_limit and the portfolio review / AI portfolio manager's in
    size_class: the amount takes the smaller one, whichever column holds it."""
    from tests.integration.test_service_api import make_service
    from marketlens.infrastructure.db import repository as repo

    decision_limit, size_class = stored
    svc = make_service(universe=10)
    with svc.sf() as s:
        repo.set_setting(s, "portfolio_cash", "100000")
        s.commit()
        row = SimpleNamespace(ticker="NVDA", final_action="BUY", result={"decision": {"size_limit": decision_limit}}, size_class=size_class)
        p = _position_plan(svc, s, row, {"current_status": "CURRENT", "actionable_now": True, "price": 100.0, "stop": 95.0})
    assert p["available"] and p["amount"] <= WEIGHT[tightest(decision_limit, size_class)] * p["nav"] + 1e-6, p
