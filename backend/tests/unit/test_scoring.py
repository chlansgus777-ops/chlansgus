import dataclasses
import inspect
from datetime import date

import pytest

from marketlens.domain import scoring
from marketlens.domain.catalysts import EventRisk
from marketlens.domain.indicators import compute_technicals
from marketlens.domain.macro import MacroImpact
from marketlens.domain.scoring import COMPONENTS, ScoringInputs, ScoringModel, score
from marketlens.domain.sector_models import RuleItem, RuleScore
from tests.conftest import make_bars

W = {"fundamental": 25, "valuation": 15, "earnings_revision": 15, "catalyst": 10, "macro": 10, "technical": 10, "risk": 10, "entry_rr": 15}


def inputs(**kw):
    t = compute_technicals(make_bars(date(2026, 9, 24)))
    base = dict(ticker="T", sector_model_id="generic", sector_model_reason="r",
                fundamental=RuleScore(0.8, 1.0, (RuleItem("revenue_growth_yoy", "Revenue growth", 0.3, 0.9, 2),)),
                valuation_absolute=RuleScore(0.5, 1.0, ()), relative_valuation=None, revisions=None, earnings=None,
                issue_score_swing=0.0, upcoming_catalyst_bias=0.0, macro=MacroImpact(0.0, ()), risk_off_active=False, beta=1.0,
                technicals=t, entry=None, event_risk=EventRisk("LOW", None, None, ()), net_debt_to_ebitda=1.0,
                avg_dollar_volume=1e8, short_interest_pct=0.03, data_completeness=1.0)
    base.update(kw)
    return ScoringInputs(**base)


def test_scoring_inputs_have_no_action_field():
    names = {f.name for f in dataclasses.fields(ScoringInputs)}
    assert not any("action" in n or "decision" in n for n in names)


def test_scoring_module_does_not_reference_decision():
    src = inspect.getsource(scoring)
    assert "domain.decision" not in src and "Action" not in src


def test_weights_must_cover_components():
    with pytest.raises(ValueError):
        ScoringModel("v", {"fundamental": 1})
    with pytest.raises(ValueError):
        ScoringModel("v", {**W, "macro": -1})


def test_breakdown_and_normalisation():
    c = score(inputs(), ScoringModel("v", W))
    assert [x.name for x in c.components] == list(COMPONENTS)
    assert 0 <= c.total <= 100
    assert c.total == pytest.approx(sum(x.subscore * x.weight for x in c.components) / sum(W.values()) * 100, abs=0.01)
    assert all(x.points <= x.weight for x in c.components)


def test_missing_component_is_conservative_and_flagged():
    c = score(inputs(revisions=None, earnings=None), ScoringModel("v", W, 0.35))
    er = c.component("earnings_revision")
    assert not er.available and er.subscore == 0.35 and "analyst_revisions" in er.missing
    assert c.completeness < 1.0


def test_deterministic():
    m = ScoringModel("v", W)
    assert score(inputs(), m).total == score(inputs(), m).total


def test_technicals_capped_to_ten_percent_of_weight():
    m = ScoringModel("v", W)
    assert m.weights["technical"] / m.total_weight <= 0.1


def test_better_fundamentals_raise_score():
    m = ScoringModel("v", W)
    lo = score(inputs(fundamental=RuleScore(0.2, 1.0, ())), m).total
    hi = score(inputs(fundamental=RuleScore(0.9, 1.0, ())), m).total
    assert hi > lo


def test_negative_issue_impact_lowers_catalyst():
    m = ScoringModel("v", W)
    pos = score(inputs(issue_score_swing=40.0), m).component("catalyst").subscore
    neg = score(inputs(issue_score_swing=-40.0), m).component("catalyst").subscore
    assert pos > neg
