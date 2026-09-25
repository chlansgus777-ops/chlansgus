import random
from datetime import date, timedelta

import pytest

from marketlens.domain.calibration import CalibrationConfig, compare_shadow, propose_weights, segment_samples
from marketlens.domain.evaluation import OutcomeSample, factor_ic, forward_return, is_mature, spearman
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import add_trading_days
from marketlens.domain.scoring import COMPONENTS
from tests.helpers import DEFAULT_WEIGHTS


def test_spearman():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 2], [1, 2]) is None
    assert spearman([1, 1, 1], [1, 2, 3]) is None


def test_maturity_uses_trading_days_not_calendar_days():
    fri = date(2026, 9, 25)
    # 5 calendar days later (Wednesday) is only 3 trading days
    assert not is_mature(fri, 5, fri + timedelta(days=5))
    assert is_mature(fri, 5, add_trading_days(fri, 5))


def test_forward_return_none_until_mature_and_ignores_future_bars():
    start = date(2026, 9, 1)
    bars = [Bar(add_trading_days(start, i), 100 + i, 100 + i, 100 + i, 100 + i, 1) for i in range(30)]
    as_of = add_trading_days(start, 3)
    assert forward_return(bars, start, 5, as_of) is None  # immature even though later bars exist in the list
    r = forward_return(bars, start, 5, add_trading_days(start, 5))
    assert r == pytest.approx(105 / 100 - 1)


def samples(n_days=30, per_day=10, signal="fundamental", noise=0.0, seed=1, start=date(2026, 1, 5), repeat_tickers=False):
    """Synthetic outcome samples. By default every sample is a different company (independent evidence);
    ``repeat_tickers=True`` re-recommends the same 10 tickers every day (overlapping holding periods)."""
    rnd = random.Random(seed)
    out = []
    d = start
    for day in range(n_days):
        for i in range(per_day):
            f = {c: rnd.random() for c in COMPONENTS}
            ret = (f[signal] - 0.5) * 0.1 + rnd.gauss(0, noise)
            name = f"T{i}" if repeat_tickers else f"T{i}-{day}"
            out.append(OutcomeSample(name, d, f, {1: ret, 5: ret, 20: ret, 60: ret}, "Tech" if i % 2 else "Energy", "Risk On"))
        d = add_trading_days(d, 1)
    return out


def test_ic_ir_and_insufficient_samples():
    s = samples(noise=0.01)
    as_of = date(2026, 9, 1)
    r = factor_ic(s, "fundamental", 20, as_of)
    assert r.ic > 0.5 and r.ir is not None and r.ir > 1
    small = factor_ic(s[:10], "fundamental", 20, as_of)
    assert small.ic is None and small.ir is None


def test_ic_excludes_immature_samples():
    s = samples(start=date(2026, 8, 20))
    r = factor_ic(s, "fundamental", 20, as_of=date(2026, 9, 1))
    assert r.samples < len(s)


def test_calibration_requires_minimum_samples():
    p = propose_weights(samples(n_days=5), DEFAULT_WEIGHTS, date(2026, 9, 1), CalibrationConfig(min_samples=100))
    assert p.status == "INSUFFICIENT_SAMPLES" and p.new_weights == p.old_weights


def test_overlapping_repeated_recommendations_do_not_count_as_independent_samples():
    """300 daily re-recommendations of the same 10 tickers are ~20 independent 20-day observations."""
    rep = samples(noise=0.02, repeat_tickers=True)
    p = propose_weights(rep, DEFAULT_WEIGHTS, date(2026, 9, 1), CalibrationConfig(min_samples=100))
    assert len(rep) == 300 and p.status == "INSUFFICIENT_SAMPLES" and p.samples <= 20
    ind = propose_weights(samples(noise=0.02), DEFAULT_WEIGHTS, date(2026, 9, 1), CalibrationConfig(min_samples=100))
    assert ind.status == "PROPOSED" and ind.samples == 300


def test_calibration_changes_are_bounded_and_zero_sum():
    cfg = CalibrationConfig(min_samples=100, max_weight_change=0.05)
    p = propose_weights(samples(noise=0.02), DEFAULT_WEIGHTS, date(2026, 9, 1), cfg)
    assert p.status == "PROPOSED"
    for k, old in DEFAULT_WEIGHTS.items():
        assert abs(p.new_weights[k] - old) <= cfg.max_weight_change * old + 1e-3
    assert sum(p.new_weights.values()) == pytest.approx(sum(DEFAULT_WEIGHTS.values()), abs=1e-3)
    assert p.new_weights["fundamental"] > DEFAULT_WEIGHTS["fundamental"]


def test_shadow_promotion_gates():
    cfg = CalibrationConfig(min_shadow_samples=50, min_ic_improvement=0.01)
    s = samples(noise=0.02)
    better = dict(DEFAULT_WEIGHTS, fundamental=60)
    cmp = compare_shadow(s, DEFAULT_WEIGHTS, better, date(2026, 1, 1), date(2026, 9, 1), cfg)
    assert cmp.promote and cmp.ic_shadow > cmp.ic_production
    worse = dict(DEFAULT_WEIGHTS, fundamental=1)
    assert not compare_shadow(s, DEFAULT_WEIGHTS, worse, date(2026, 1, 1), date(2026, 9, 1), cfg).promote
    # out-of-sample only: a shadow started after all samples has nothing to compare → no promotion
    late = compare_shadow(s, DEFAULT_WEIGHTS, better, date(2026, 8, 30), date(2026, 9, 1), cfg)
    assert not late.promote and late.samples == 0


def test_segment_fallback_to_global():
    s = samples(n_days=3)
    seg, is_seg = segment_samples(s, "sector", "Tech", 1000)
    assert not is_seg and len(seg) == len(s)
    seg2, is_seg2 = segment_samples(s, "sector", "Tech", 5)
    assert is_seg2 and all(x.sector == "Tech" for x in seg2)
