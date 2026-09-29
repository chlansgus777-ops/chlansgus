"""Return signals (domain/signals.py, PREREGISTRATION §13): each computed exactly as registered, None when it cannot be
computed, never from a price or report after the analysis time."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from marketlens.domain.earnings import EarningsReport
from marketlens.domain.fundamentals import QuarterlyFinancials
from marketlens.domain.market import Bar
from marketlens.domain.signals import ear, fscore, grade, high52, percentile, return_signals, rs_raw, rs_rank

D0 = date(2025, 1, 2)


def bars(closes: list[float], start: date = D0) -> list[Bar]:
    out, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append(Bar(d, c, c, c, c, 1e6))
        d += timedelta(days=1)
    return out


def test_high52_uses_252_closes_and_the_registered_scale():
    b = bars([100.0] * 251 + [200.0] + [180.0] * 10)
    s = high52(b)
    assert s.value == pytest.approx(0.9) and s.sub == pytest.approx((0.9 - 0.70) / 0.28, abs=1e-4)
    assert high52(bars([100.0] * 200)).sub is None
    assert high52(bars([50.0] * 252 + [70.0])).sub == 1.0  # a new high


def test_rs_is_the_ibd_weighting_and_a_universe_percentile():
    closes = [100.0] * 300
    closes[-1] = 110.0  # +10% over every window
    assert rs_raw(closes) == pytest.approx(0.10)
    assert rs_raw([1.0] * 252) is None
    u = sorted([-0.2, -0.1, 0.0, 0.05, 0.1, 0.2, 0.3])
    assert percentile(u, 0.1) == pytest.approx((4 + 0.5) / 7)
    s = rs_rank(bars(closes), percentile(u, 0.1), len(u))
    assert s.value == round(percentile(u, 0.1) * 99) and "7개 종목 대비" in s.text
    assert rs_rank(bars(closes), None, 0).sub is None


def q(i: int, ni: float, cfo: float, rev: float, gp: float, debt: float, eq: float, cash: float, shares: float) -> QuarterlyFinancials:
    end = date(2023, 3, 31) + timedelta(days=91 * i)
    return QuarterlyFinancials(end, end + timedelta(days=40), f"Q{i}", "t", revenue=rev, gross_profit=gp, net_income=ni, operating_cash_flow=cfo,
                               total_debt=debt, total_equity=eq, cash=cash, shares_diluted=shares)


def test_fscore_all_nine_tests():
    old = [q(i, 10, 12, 100, 40, 50, 100, 20, 1000) for i in range(4)]
    better = [q(i, 14, 18, 120, 52, 40, 105, 30, 1000) for i in range(4, 8)]
    s = fscore(old + better)
    assert s.value == 9 and s.sub == 1.0 and len(s.detail) == 9
    worse = [q(i, -2, -3, 90, 30, 70, 90, 10, 1100) for i in range(4, 8)]
    assert fscore(old + worse).value == 0
    assert fscore(old[:3]).sub is None  # fewer than 4 quarters
    assert fscore(old + better[:1]).sub is None  # year-ago TTM missing: only level tests → fewer than 7


def test_ear_window_market_adjusted_and_age_limited():
    b = bars([100.0] * 30 + [100.0, 110.0, 112.0] + [112.0] * 10)
    spy = bars([400.0] * 31 + [404.0, 408.0] + [408.0] * 10)
    rel_day = b[30].day
    r = EarningsReport(rel_day, "Q", "sec", released_at=datetime.combine(rel_day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=21, minutes=5))  # 17:05 ET
    s = ear(b, spy, [r])
    # after the close on day 30: t0 = day 30 close (100), t1 = day 32 close (112); SPY 400 → 408
    assert s.value == pytest.approx(0.12 - 0.02, abs=1e-4)
    old = bars([100.0] * 30 + [100.0, 110.0, 112.0] + [112.0] * 70)
    assert ear(old, bars([400.0] * 103), [r]).sub is None  # more than 60 sessions ago
    fresh = bars([100.0] * 31)
    assert ear(fresh, bars([400.0] * 31), [EarningsReport(fresh[-1].day, "Q", "sec")]).sub is None  # the reaction is not complete


def test_component_needs_two_signals_and_grades_are_fixed():
    few = return_signals(bars([100.0] * 60), bars([400.0] * 60), [], [], None, 0)
    assert few.subscore is None and few.coverage == 0.0
    assert [grade(x) for x in (0.8, 0.65, 0.45, 0.3, 0.29, None)] == ["A", "B", "C", "D", "F", None]
