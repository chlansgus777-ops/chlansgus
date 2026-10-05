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
    # at exactly max buy, R/R equals the minimum (the exact level, before the cent shown on screen)
    rr_at_max = (p.target1 - p.max_buy_exact) / (p.max_buy_exact - p.stop_exact)
    assert rr_at_max == pytest.approx(2.0, abs=0.02)
    # the shown max buy is rounded DOWN (independent review F02), so buying at it never gives less than the minimum
    assert p.max_buy <= p.max_buy_exact < p.max_buy + 0.01
    assert (p.target1 - p.max_buy) / (p.max_buy - p.stop_exact) >= 2.0
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


def test_the_first_target_does_not_jump_when_the_price_crosses_one_atr_below_a_resistance():
    """Owner 2026-10-05 (MU): $1,065.70 skipped a resistance less than 1 ATR above and aimed at the next one (max buy
    $1,086.17); at $1,059.82 that resistance counted (max buy $1,037.41). The nearest resistance above the price is
    the first target either way, so a small price move no longer moves the plan."""
    from dataclasses import replace

    t = compute_technicals(make_bars(date(2026, 9, 24)))
    a = t.atr14
    near = t.last_close + 0.6 * a
    t2 = replace(t, resistances=(near, t.last_close + 4 * a), high_52w=t.last_close + 6 * a)
    up = build_entry_plan(t.last_close + 0.15 * a, t2)
    down = build_entry_plan(t.last_close - 0.15 * a, t2)
    assert up.target1 == pytest.approx(near, abs=0.01) and down.target1 == pytest.approx(near, abs=0.01)
    assert up.max_buy == pytest.approx(down.max_buy, abs=0.01)  # the plan is the same; only where the price sits moved
    assert (up.rr_at_current or 0) < 2.0  # a resistance this close leaves little reward: the plan says wait
