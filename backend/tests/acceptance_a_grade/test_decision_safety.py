"""A-grade acceptance: decision safety. Each test reproduces an audit finding through the production code path."""

from __future__ import annotations

from marketlens.domain.decision import decide
from marketlens.domain.enums import Action, HardVeto
from marketlens.domain.scoring import COMPONENTS, ComponentScore, ScoreCard
from tests.helpers import DEFAULT_WEIGHTS, ctx, plan


def _card(measured: dict[str, float | None], coverage: dict[str, float] | None = None, missing_default: float = 0.35) -> ScoreCard:
    """Build a card the way ``scoring.score`` does: missing inputs blend toward the conservative default."""
    comps = []
    for n in COMPONENTS:
        m = measured.get(n)
        cov = (coverage or {}).get(n, 1.0 if m is not None else 0.0)
        avail = m is not None and cov >= 0.3
        sub = m * cov + missing_default * (1 - cov) if avail else missing_default  # type: ignore[operator]
        comps.append(ComponentScore(n, DEFAULT_WEIGHTS[n], sub, avail, (), coverage=cov, measured=m if avail else None))
    return ScoreCard("JPM", "test", tuple(comps), "financial", "test")


def test_missing_data_is_never_sell_evidence():
    """Audit P0: a bank whose sector KPIs are unavailable scored ~48 and a held position became REDUCE.
    Here every *measured* input is fine (0.6); only the missing ones drag the buy-side score down."""
    c = _card({"fundamental": 0.6, "valuation": 0.6, "earnings_revision": None, "catalyst": None, "macro": 0.6, "technical": 0.6, "risk": 0.6, "entry_rr": 0.6},
              coverage={"fundamental": 0.5, "valuation": 0.5})
    assert c.total < 55 <= c.sell_side_total  # buy-side conservative, sell-side neutral
    d = decide(c, plan(), ctx(held=True))
    assert d.action not in (Action.REDUCE, Action.SELL)


def test_measured_weakness_still_sells():
    """The fix must not hide real deterioration: fully measured bad fundamentals still produce SELL."""
    c = _card({n: 0.3 for n in COMPONENTS})
    assert decide(c, plan(), ctx(held=True)).action == Action.SELL


def test_sector_model_that_cannot_judge_blocks_buy_and_sell():
    c = _card({n: 0.9 for n in COMPONENTS})
    for held in (False, True):
        d = decide(c, plan(), ctx(held=held, model_coverage_gaps=("valuation(25%)",)))
        assert d.action == Action.DATA_INSUFFICIENT and HardVeto.INSUFFICIENT_MODEL_COVERAGE in d.vetoes
        assert d.confidence <= 30


def test_price_fact_still_sells_when_model_coverage_is_missing():
    c = _card({n: 0.9 for n in COMPONENTS})
    d = decide(c, plan(), ctx(held=True, model_coverage_gaps=("fundamental(10%)",), prior_stop_breached=True))
    assert d.action == Action.SELL


# ---------------------------------------------------------------- calibration fails closed


def test_calibration_never_promotes_when_safety_metrics_are_missing():
    """Audit P1: 100 samples, IC improved, hit rate and downside None → promote=True."""
    from datetime import date

    from marketlens.domain.calibration import CalibrationConfig, compare_shadow
    from marketlens.domain.evaluation import OutcomeSample
    from marketlens.domain.market_calendar import add_trading_days

    # 100 samples on 100 different days, one per day → no cross-section, so top-quintile stats are unmeasurable
    samples, d = [], date(2026, 1, 5)
    for i in range(100):
        f = {c: (i % 10) / 10 for c in DEFAULT_WEIGHTS}
        samples.append(OutcomeSample(f"T{i}", d, f, {20: (i % 10) / 100}, "Tech", "Risk On"))
        d = add_trading_days(d, 21)  # non-overlapping horizons
    cfg = CalibrationConfig(min_shadow_samples=10, min_ic_improvement=-1.0)
    cmp = compare_shadow(samples, DEFAULT_WEIGHTS, dict(DEFAULT_WEIGHTS, fundamental=40), date(2026, 1, 1), date(2040, 1, 1), cfg)
    assert cmp.hit_rate_shadow is None and not cmp.promote
    assert any("필수 안전 지표" in r for r in cmp.reasons)


def test_auto_promotion_is_off_by_default():
    from marketlens.domain.calibration import CalibrationConfig

    assert CalibrationConfig().auto_promote is False


# ---------------------------------------------------------------- portfolio valuation policy


def test_missing_holding_price_is_never_valued_at_cost_and_caps_new_buys():
    """Audit P1: the recommendation engine silently valued a price-less holding at its cost basis."""
    from datetime import date

    from marketlens.domain.portfolio import CandidateProfile, Holding, Portfolio, SizeClass, common_valuation, review_candidate

    pf = Portfolio((Holding("AAA", 10, 100, "Tech"), Holding("BBB", 10, 50, "Energy")), 100_000)
    closes = {"AAA": {date(2026, 9, 23): 110.0, date(2026, 9, 24): 111.0}, "BBB": {}}
    day, prices, missing = common_valuation(closes, ["AAA", "BBB"])
    assert day == date(2026, 9, 24) and prices == {"AAA": 111.0} and missing == ("BBB",)
    r = review_candidate(pf, prices, CandidateProfile("CCC", "Health", (), 0.0, {}), {}, valuation_day=day)
    assert r.valuation_status == "PARTIAL" and r.missing_prices == ("BBB",)
    assert r.size_cap in (SizeClass.SMALL, SizeClass.WATCH) and any("PRICE MISSING" in w for w in r.warnings)
    assert "Energy" not in r.sector_weights  # unknown value is not invented from the cost basis


def test_sector_model_reports_coverage_critical_missing_and_usable():
    from marketlens.domain.sector_models import MetricRule, score_rules

    rules = (MetricRule("rotce", "ROTCE", 3, 0.06, 0.18, critical=True), MetricRule("loan_growth", "대출", 1, -0.03, 0.08), MetricRule("roe", "ROE", 1, 0.06, 0.16))
    ok = score_rules(rules, {"rotce": 0.15, "loan_growth": 0.05}, 0.4)
    assert ok.usable and ok.coverage == 0.8 and ok.critical_missing == ()
    no_core = score_rules(rules, {"loan_growth": 0.05, "roe": 0.12}, 0.1)  # 40% coverage but the core metric is missing
    assert not no_core.usable and no_core.critical_missing == ("rotce",)


def test_stop_has_one_meaning_close_based_exit_intraday_warning():
    c = _card({n: 0.9 for n in COMPONENTS})
    assert decide(c, plan(), ctx(held=True, previous_action=Action.BUY, prior_stop_breached=True)).action == Action.SELL
    held_intraday = decide(c, plan(), ctx(held=True, previous_action=Action.BUY, intraday_stop_breach=True))
    assert held_intraday.action not in (Action.SELL, Action.ADD) and any("종가 확인 전" in r for r in held_intraday.reasons)
    new_intraday = decide(c, plan(), ctx(held=False, previous_action=Action.BUY, intraday_stop_breach=True))
    assert new_intraday.action not in (Action.BUY, Action.BUY_SMALL, Action.ADD)
