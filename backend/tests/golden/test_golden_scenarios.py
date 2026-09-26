"""Golden scenarios on frozen point-in-time fixtures. We check invariants, not a forced BUY/SELL."""

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from marketlens.application.codec import decode
from marketlens.application.pipeline import AnalysisInputs, run_analysis
from marketlens.domain.catalysts import CatalystEvent, CatalystType
from marketlens.domain.enums import BULLISH_ACTIONS, Action, DataQuality, HardVeto
from marketlens.domain.fundamentals import QuarterlyFinancials
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import add_trading_days

FIX = Path(__file__).parent / "fixtures"
GOLDEN = ("NVDA", "AMZN", "JPM", "XOM", "TSM")
EXPECTED_MODEL = {"NVDA": "semiconductor", "AMZN": "internet", "JPM": "financial", "XOM": "energy", "TSM": "semiconductor"}


def load(t: str) -> AnalysisInputs:
    return decode(AnalysisInputs, json.loads((FIX / f"{t}.json").read_text()))


@pytest.mark.parametrize("t", GOLDEN)
def test_sector_model_and_breakdown(cfg, t):
    r = run_analysis(load(t), cfg)
    assert r.sector_model_id == EXPECTED_MODEL[t]
    assert 0 <= r.scorecard.total <= 100
    assert r.price is not None and r.price_quality in (DataQuality.FRESH, DataQuality.DELAYED)
    assert r.session and r.price_source and r.price_timestamp
    for c in r.scorecard.components:
        assert 0 <= c.points <= c.weight


def test_tsm_is_a_supported_adr(cfg):
    r = run_analysis(load("TSM"), cfg)
    assert r.security.is_adr and r.security.country_of_incorporation == "TW"
    assert r.metrics is not None


def test_jpm_not_scored_with_semiconductor_metrics(cfg):
    r = run_analysis(load("JPM"), cfg)
    metrics = {i.metric for i in r.fundamental_rules.items}
    assert "cet1" in metrics and "gross_margin" not in metrics


@pytest.mark.parametrize("t", GOLDEN)
def test_deterministic_replay(cfg, t):
    inp = load(t)
    a, b = run_analysis(inp, cfg), run_analysis(inp, cfg)
    assert a.scorecard.total == b.scorecard.total and a.decision == b.decision and a.input_fingerprint == b.input_fingerprint


@pytest.mark.parametrize("t", GOLDEN)
def test_stale_price_never_buy(cfg, t):
    inp = load(t)
    stale = replace(inp, quote=replace(inp.quote, timestamp=inp.as_of - timedelta(days=3)))
    r = run_analysis(stale, cfg)
    assert r.decision.action not in BULLISH_ACTIONS
    assert HardVeto.STALE_PRICE in r.decision.vetoes


@pytest.mark.parametrize("t", GOLDEN)
def test_missing_core_financials_never_full_buy(cfg, t):
    r = run_analysis(replace(load(t), quarters=()), cfg)
    assert r.decision.action == Action.DATA_INSUFFICIENT


@pytest.mark.parametrize("t", GOLDEN)
def test_conflicting_price_blocks_decision(cfg, t):
    r = run_analysis(replace(load(t), provider_conflicts=("price: 100 vs 130",)), cfg)
    assert r.decision.action == Action.DATA_INSUFFICIENT and HardVeto.SEVERE_DATA_CONFLICT in r.decision.vetoes


@pytest.mark.parametrize("t", GOLDEN)
def test_severe_consensus_conflict_blocks_buying_through_the_pipeline(cfg, t):
    """Evaluation 2: a 50% analyst-provider conflict left BUY with no veto."""
    c = "analyst: analyst:SEVERE_DATA_CONFLICT alphavantage 1 vs finnhub 0.5 (차이 50.0%)"
    r = run_analysis(replace(load(t), provider_conflicts=(c,)), cfg)
    assert r.decision.action not in BULLISH_ACTIONS and HardVeto.SEVERE_ESTIMATE_CONFLICT in r.decision.vetoes


@pytest.mark.parametrize("t", GOLDEN)
def test_extreme_event_risk_limits_sizing(cfg, t):
    inp = load(t)
    ev = CatalystEvent("E", CatalystType.EARNINGS, add_trading_days(inp.as_of.date(), 1), "earnings", (t,), 1.0, expected_move=0.25)
    r = run_analysis(replace(inp, events=inp.events + (ev,)), cfg)
    assert r.event_risk.level == "EXTREME"
    assert r.decision.action != Action.BUY
    assert HardVeto.EXTREME_EVENT_RISK in r.decision.vetoes


@pytest.mark.parametrize("t", GOLDEN)
def test_future_quarter_is_ignored_point_in_time(cfg, t):
    inp = load(t)
    base = run_analysis(inp, cfg)
    q = inp.quarters[-1]
    future = QuarterlyFinancials(period_end=inp.as_of.date() + timedelta(days=1), filed_date=inp.as_of.date() + timedelta(days=30), fiscal_label="FUTURE",
                                 source="x", revenue=(q.revenue or 1) * 10, net_income=(q.net_income or 1) * 10, eps_diluted=99.0)
    r = run_analysis(replace(inp, quarters=inp.quarters + (future,)), cfg)
    assert r.scorecard.total == base.scorecard.total and r.metrics == base.metrics


@pytest.mark.parametrize("t", GOLDEN)
def test_future_bars_are_ignored(cfg, t):
    inp = load(t)
    base = run_analysis(inp, cfg)
    last = inp.bars[-1]
    fut = tuple(Bar(inp.as_of.date() + timedelta(days=i), last.close * 3, last.close * 3, last.close * 3, last.close * 3, last.volume) for i in range(1, 5))
    r = run_analysis(replace(inp, bars=inp.bars + fut), cfg)
    assert r.technicals == base.technicals and r.scorecard.total == base.scorecard.total


def test_thesis_invalidation_through_issue(cfg):
    inp = load("NVDA")
    r = run_analysis(inp, cfg)
    assert any(i.issue_id.startswith("ISSUE_EXPORT_CONTROL") for i in r.issue_impacts)
    strict = tuple(replace(c, issue_threshold=-1.0) if c.issue_category == "Export Control" else c for c in inp.thesis_conditions)
    r2 = run_analysis(replace(inp, thesis_conditions=strict), cfg)
    assert r2.thesis_invalidated and r2.decision.action not in BULLISH_ACTIONS


def test_explanations_are_evidence_linked(cfg):
    r = run_analysis(load("NVDA"), cfg)
    ids = {e.evidence_id for e in r.evidence}
    linked = [v for v in r.reason_evidence.values() if v]
    assert linked and all(i in ids for v in linked for i in v)
    assert any(e.evidence_id.startswith("ANALYST_EPS_REVISION_30D_NVDA_") for e in r.evidence)
