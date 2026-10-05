"""The short-term strategies of PREREGISTRATION §17 (backtest/swing.py): indicators, split basis, no look-ahead, fills."""

from datetime import date, timedelta

import numpy as np

from marketlens.backtest import swing as sw


def _days(n: int, start: date = date(2020, 1, 1)) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _series(key: str, closes: list[float], days: list[date] | None = None, vol: float = 1e6, opens: list[float] | None = None,
            lows: list[float] | None = None, highs: list[float] | None = None) -> sw.Series:
    days = days or _days(len(closes))
    c = np.array(closes, dtype=float)
    o = np.array(opens if opens else closes, dtype=float)
    lo = np.array(lows if lows else np.minimum(o, c) * 0.995, dtype=float)
    h = np.array(highs if highs else np.maximum(o, c) * 1.005, dtype=float)
    s = sw.Series(key, days, o, h, lo, c, np.full(len(c), vol), c.copy())
    sw.indicators(s)
    return s


def test_indicators():
    x = np.array([1.0, 2, 3, 4, 5])
    assert np.allclose(sw.sma(x, 3)[2:], [2, 3, 4]) and np.isnan(sw.sma(x, 3)[1])
    # the breakout reference is the highest BEFORE today: a new high today is above it
    assert np.isnan(sw.rolling_max_prior(x, 3)[2]) and sw.rolling_max_prior(x, 3)[3] == 3 and sw.rolling_min_prior(x, 3)[4] == 2
    # Wilder: seed = mean of the first n, then (prev·(n−1)+x)/n
    w = sw.wilder(np.array([2.0, 4, 6, 8]), 2)
    assert np.isnan(w[0]) and np.allclose(w[1:], [3, 4.5, 6.25])
    up = sw.rsi(np.array([10.0, 11, 12, 13]), 2)
    assert up[-1] == 100.0  # only gains
    # by hand: diffs +1 −1 −1 −1 → gains 0.5, 0.25, 0.125 · losses 0.5, 0.75, 0.875 → 100 − 100/(1 + 1/7) = 12.5
    dn = sw.rsi(np.array([10.0, 11, 10, 9, 8]), 2)
    assert abs(dn[-1] - 12.5) < 1e-9


def test_split_basis_divides_earlier_prices_and_keeps_ratios():
    days = _days(4)
    rows = [(100, 101, 99, 100, 10), (100, 101, 99, 100, 10), (50, 51, 49, 50, 20), (51, 52, 50, 51, 20)]
    o, h, lo, c, v, raw = sw.adjusted(days, rows, [(days[2], 2.0)])
    assert list(c) == [50, 50, 50, 51] and list(v) == [20, 20, 20, 20] and list(raw) == [100, 100, 50, 51]


def test_a_signal_never_reads_a_later_bar():
    rng = np.random.default_rng(1)
    closes = list(100 * np.cumprod(1 + rng.normal(0.001, 0.02, 400)))
    a = _series("X", closes, vol=1e6)
    future = closes[:300] + [c * 3 for c in closes[300:]]
    b = _series("X", future, vol=1e6)
    up = {d: True for d in a.days}
    for st in sw.STRATEGIES:
        sa, sb = sw.signals(st, a, up), sw.signals(st, b, up)
        assert np.array_equal(np.isnan(sa[:300]), np.isnan(sb[:300])), st


def _uptrend_dip(n: int = 300) -> list[float]:
    closes = [50 + 0.2 * i for i in range(n)]
    closes[-3:] = [closes[-4] * 0.99, closes[-4] * 0.98, closes[-4] * 0.97]  # a three-day dip in a rising trend
    return closes


def test_strategy_a_buys_at_the_next_open_and_pays_costs_on_both_sides():
    closes = _uptrend_dip() + [0.0] * 4
    base = closes[-5]
    closes[-4:] = [base * 0.975, base * 1.02, base * 1.03, base * 1.04]  # next day opens lower, then recovers
    days = _days(len(closes))
    opens = closes[:]
    s = _series("X", closes, days, vol=1e6, opens=opens)
    spy = _series("SPY", [100 + i for i in range(len(closes))], days, vol=1e8)
    sig = sw.signals("A", s, {d: True for d in days})
    entry_i = int(np.flatnonzero(~np.isnan(sig))[0]) + 1
    res = sw.simulate("A", {"X": s}, days, spy, cost=0.001)
    t = res["trades"][0]
    assert t.entry_day == days[entry_i] and t.entry_px == opens[entry_i]
    assert t.exit_day > t.entry_day
    assert abs(t.ret - (t.exit_px * 0.999 / (t.entry_px * 1.001) - 1)) < 1e-12  # costs on both sides


def test_a_stop_fills_at_the_stop_or_the_lower_open_and_a_delisting_loses_30_percent():
    closes = _uptrend_dip()
    days = _days(len(closes) + 3)
    s = _series("X", closes + [closes[-1]] * 3, days, vol=1e6)
    i = len(closes)  # entry day
    s.lo[i + 1] = s.o[i] * 0.5  # crashes through the stop the next day
    s.o[i + 1] = s.o[i] * 0.6
    spy = _series("SPY", [100 + k for k in range(len(days))], days, vol=1e8)
    res = sw.simulate("A", {"X": s}, days, spy)
    t = res["trades"][0]
    assert t.reason == "stop" and t.exit_px == s.o[i + 1]  # gapped below the stop: the open
    # a delisting: the bars end and the calendar goes on
    days2 = _days(len(closes) + 20)
    s2 = _series("Y", closes + [closes[-1]], days2[: len(closes) + 1], vol=1e6)
    spy2 = _series("SPY", [100 + k for k in range(len(days2))], days2, vol=1e8)
    res2 = sw.simulate("A", {"Y": s2}, days2, spy2)
    t2 = res2["trades"][0]
    assert t2.reason == "delisted" and abs(t2.exit_px - s2.c[-1] * 0.7) < 1e-9


def test_at_most_ten_positions_and_none_twice():
    closes = _uptrend_dip()
    days = _days(len(closes) + 2)
    series = {f"S{k}": _series(f"S{k}", closes + [closes[-1]] * 2, days, vol=1e6) for k in range(15)}
    spy = _series("SPY", [100 + k for k in range(len(days))], days, vol=1e8)
    res = sw.simulate("A", series, days, spy)
    entries = {t.key for t in res["trades"]}
    assert len(entries) <= 10


def test_the_adoption_rule_needs_all_four():
    good = {"cagr": 0.1, "sharpe": 1.2, "trades": 40}
    spy = {"cagr": 0.12, "sharpe": 1.0}
    v = sw.verdict({"cagr": 0.05}, good, spy)
    assert v["adopt"] and not v["beats_spy_holdout"]
    assert not sw.verdict({"cagr": -0.01}, good, spy)["adopt"]
    assert not sw.verdict({"cagr": 0.05}, good | {"trades": 10}, spy)["adopt"]
    assert not sw.verdict({"cagr": 0.05}, good | {"sharpe": 0.9}, spy)["adopt"]
