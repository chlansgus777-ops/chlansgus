from datetime import date

import pytest

from marketlens.domain.earnings import AnalystSnapshot
from marketlens.domain.enums import Exchange
from marketlens.domain.market import Security
from marketlens.domain.sector_models import MetricRule, score_rules, select_sector_model
from marketlens.domain.valuation import compute_multiples, percentile_rank, relative_valuation
from tests.unit.test_fundamentals import q
from marketlens.domain.fundamentals import compute_metrics


def sec(t, sector, industry):
    return Security(t, t, Exchange.NASDAQ, sector, industry, 1e10)


def test_nvda_and_jpm_use_different_models(cfg):
    m1, why1 = select_sector_model(sec("NVDA", "Technology", "Semiconductors"), cfg.sector_models)
    m2, why2 = select_sector_model(sec("JPM", "Financial Services", "Banks—Diversified"), cfg.sector_models)
    assert m1.model_id == "semiconductor" and m2.model_id == "financial"
    assert {r.metric for r in m1.fundamental_rules} != {r.metric for r in m2.fundamental_rules}
    assert "cet1" in {r.metric for r in m2.fundamental_rules}
    assert "semiconductor" in why1.lower() and "bank" in why2.lower()


@pytest.mark.parametrize("sector,industry,expected", [
    ("Technology", "Software—Infrastructure", "software"),
    ("Communication Services", "Internet Content & Information", "internet"),
    ("Consumer Cyclical", "Internet Retail", "internet"),
    ("Consumer Defensive", "Household & Personal Products", "consumer"),
    ("Healthcare", "Biotechnology", "biotech"),
    ("Healthcare", "Drug Manufacturers—General", "healthcare"),
    ("Energy", "Oil & Gas Integrated", "energy"),
    ("Industrials", "Aerospace & Defense", "industrials"),
    ("Real Estate", "REIT—Industrial", "reit"),
    ("Utilities", "Utilities—Regulated Electric", "utilities"),
    ("Basic Materials", "Gold", "generic"),
])
def test_sector_model_selection(cfg, sector, industry, expected):
    assert select_sector_model(sec("X", sector, industry), cfg.sector_models)[0].model_id == expected


def test_rule_subscore_direction():
    hi = MetricRule("x", "x", 1, bad=0.0, good=1.0)
    lo = MetricRule("pe", "pe", 1, bad=40, good=10)
    assert hi.subscore(0.5) == 0.5 and hi.subscore(2) == 1.0
    assert lo.subscore(10) == 1.0 and lo.subscore(40) == 0.0 and lo.subscore(25) == pytest.approx(0.5)


def test_rule_coverage_threshold():
    rules = [MetricRule("a", "a", 1, 0, 1), MetricRule("b", "b", 3, 0, 1)]
    assert score_rules(rules, {"a": 1.0}, 0.5).subscore is None  # 25% coverage
    rs = score_rules(rules, {"a": 1.0, "b": 0.0}, 0.5)
    assert rs.subscore == pytest.approx(0.25) and rs.coverage == 1.0


def test_multiples_and_negative_earnings():
    m = compute_metrics([q(i, 100 + i, eps=0.5) for i in range(8)])
    a = AnalystSnapshot(date(2026, 1, 1), "t", forward_eps=2.5, forward_eps_growth=0.25)
    v = compute_multiples(50.0, m, a)
    assert v.forward_pe == pytest.approx(20.0)
    assert v.peg == pytest.approx(20.0 / 25.0)
    assert v.market_cap == 5000 and v.enterprise_value == 5000 + 300 - 100
    neg = compute_metrics([q(i, 100, eps=-0.5) for i in range(8)])
    assert compute_multiples(50.0, neg, None).trailing_pe is None


def test_rate_adjusted_valuation():
    m = compute_metrics([q(i, 100, eps=0.5) for i in range(8)])
    v = compute_multiples(50.0, m, AnalystSnapshot(date(2026, 1, 1), "t", forward_eps=2.5))  # fwd EY 5%
    rv = relative_valuation(v, "forward_pe", [15.0] * 10, [25.0, 30.0, 35.0], us10y=0.045)
    assert rv.equity_risk_spread == pytest.approx(0.005)
    assert rv.history_percentile == 1.0
    assert rv.premium_to_peers == pytest.approx(20 / 30 - 1)
    rv2 = relative_valuation(v, "forward_pe", [], [], us10y=0.06)
    assert rv2.equity_risk_spread < 0 and any("Treasury" in n for n in rv2.notes)
    assert percentile_rank(1.0, [1, 2, 3]) is None  # too little history
