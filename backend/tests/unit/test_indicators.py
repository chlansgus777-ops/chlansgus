from datetime import date, timedelta

import pytest

from marketlens.domain.indicators import anchored_vwap, atr, cluster_levels, compute_technicals, ema, relative_strength, rsi, sma, volume_ratio
from marketlens.domain.market import Bar
from tests.conftest import make_bars


def test_sma_ema():
    assert sma([1, 2, 3, 4], 2) == 3.5
    assert sma([1], 2) is None
    assert ema([1.0] * 30, 10) == pytest.approx(1.0)


def test_rsi_extremes():
    assert rsi([float(i) for i in range(30)]) == 100.0
    assert rsi([float(30 - i) for i in range(30)]) == pytest.approx(0.0)
    assert rsi([1.0] * 30) == 50.0


def test_atr_constant_range():
    bars = [Bar(date(2026, 1, 1) + timedelta(days=i), 10, 11, 9, 10, 100) for i in range(30)]
    assert atr(bars, 14) == pytest.approx(2.0)


def test_anchored_vwap_weights_volume():
    d = date(2026, 1, 1)
    bars = [Bar(d, 10, 10, 10, 10, 100), Bar(d + timedelta(days=1), 20, 20, 20, 20, 300)]
    assert anchored_vwap(bars, d) == pytest.approx(17.5)
    assert anchored_vwap(bars, d + timedelta(days=1)) == pytest.approx(20.0)


def test_relative_strength_and_volume_ratio():
    assert relative_strength([100, 110], [100, 105], 1) == pytest.approx(0.05)
    assert volume_ratio([100] * 20 + [200], 20) == pytest.approx(2.0)


def test_cluster_levels():
    assert cluster_levels([100, 100.5, 120], 1.0) == [100.25, 120]


def test_compute_technicals_supports_below_resistances_above():
    bars = make_bars(date(2026, 9, 24))
    t = compute_technicals(bars)
    assert t.sma200 is not None and t.atr14 is not None
    assert all(s < t.last_close for s in t.supports)
    assert all(r > t.last_close for r in t.resistances)
