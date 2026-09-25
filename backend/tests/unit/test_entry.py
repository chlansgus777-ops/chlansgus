from datetime import date

import pytest

from marketlens.domain.entry import EntryConfig, ThesisCondition, build_entry_plan, evaluate_thesis
from marketlens.domain.indicators import compute_technicals
from tests.conftest import make_bars


def test_max_buy_derived_from_min_rr():
    t = compute_technicals(make_bars(date(2026, 9, 24)))
    cfg = EntryConfig(min_rr=2.0)
    p = build_entry_plan(t.last_close, t, cfg)
    assert p is not None
    assert p.max_buy == pytest.approx((p.target1 + 2 * p.stop) / 3, abs=0.02)
    # at exactly max buy, R/R equals the minimum
    rr_at_max = (p.target1 - p.max_buy) / (p.max_buy - p.stop)
    assert rr_at_max == pytest.approx(2.0, abs=0.02)
    assert p.stop < p.ideal_entry <= p.max_buy < p.target1 <= p.target2


def test_price_above_max_buy_is_not_in_zone():
    t = compute_technicals(make_bars(date(2026, 9, 24)))
    p = build_entry_plan(t.last_close, t)
    far = build_entry_plan(p.max_buy * 1.2, t)
    assert far is not None and (not far.in_buy_zone or far.max_buy >= far.current_price)


def test_no_plan_without_atr_or_price():
    t = compute_technicals(make_bars(date(2026, 9, 24), n=10))
    assert build_entry_plan(100.0, t) is None
    assert build_entry_plan(None, compute_technicals(make_bars(date(2026, 9, 24)))) is None  # type: ignore[arg-type]


def test_thesis_invalidation_separate_from_price_stop():
    conds = [ThesisCondition("g", "growth collapse", "revenue_growth_yoy", "<", 0.0), ThesisCondition("reg", "regulation worse")]
    bad, why = evaluate_thesis(conds, {"revenue_growth_yoy": -0.05}, set())
    assert bad and "growth collapse" in why[0]
    bad2, _ = evaluate_thesis(conds, {"revenue_growth_yoy": 0.2}, {"reg"})
    assert bad2
    ok, _ = evaluate_thesis(conds, {"revenue_growth_yoy": None}, set())
    assert not ok
