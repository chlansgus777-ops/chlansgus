"""PREREGISTRATION §20 rules (domain/strategies.py) and their portfolio runs (backtest/portfolio.py) on synthetic bars —
rule checks only, never a performance figure."""

from datetime import date, timedelta

import numpy as np

from marketlens.backtest import portfolio as P
from marketlens.domain import strategies as S



def _days(n: int, start: date = date(2019, 1, 1)) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _rows(closes, vol=2e6):
    v = vol if isinstance(vol, list) else [vol] * len(closes)
    return [(c, c * 1.002, c * 0.998, c, x) for c, x in zip(closes, v)]


def test_momentum_skips_the_latest_month_and_reads_no_later_bar():
    c = np.arange(1.0, 401.0)
    m = S.momentum(c)
    assert np.isnan(m[251]) and abs(m[300] - (c[279] / c[48] - 1)) < 1e-12
    c2 = c.copy()
    c2[300:] *= 3  # later bars change nothing before them
    assert np.array_equal(S.momentum(c2)[:300], m[:300], equal_nan=True)
    c3 = c.copy()
    c3[290:301] *= 5  # the skipped month (i−20 … i) does not count either
    assert S.momentum(c3)[300] == m[300]


def test_month_ends_and_the_large_cap_bar():
    days = [date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 29), date(2024, 3, 1)]
    assert S.month_ends(days) == {date(2024, 1, 31), date(2024, 2, 29)}
    v = np.array([5.0, 1.0, np.nan, 9.0, 3.0])
    assert S.cap_threshold(v, 2) == 5.0 and S.cap_threshold(v, 10) == 1.0
    assert S.momentum_ranks({"B": 0.2, "A": 0.2, "C": float("nan"), "D": 0.5}) == ["D", "A", "B"]


def _sec(key, closes, days, sector="Tech", vol=2e6):
    return P.make_sec(key, key, sector, days, _rows(closes, vol=vol), [])


def _world(n=420, names=30, spy_up=True):
    days = _days(n, date(2018, 1, 1))
    rng = np.random.default_rng(7)
    secs = {}
    for j in range(names):
        drift = 0.0002 + 0.0001 * j  # higher j → stronger momentum
        secs[f"S{j:02d}"] = _sec(f"S{j:02d}", list(50 * np.cumprod(1 + drift + rng.normal(0, 0.002, n))), days, sector=f"sec{j % 8}")
    spy_c = [100 + (0.1 if spy_up else -0.1) * i for i in range(n)]
    spy = P.make_sec("SPY", "SPY", "ETF", days, _rows([max(c, 1.0) for c in spy_c], vol=1e8), [])
    return secs, spy, days


def _run(variant, secs, spy, days):
    up = P.spy_up_map(spy)
    cands, xexit = P.s20_candidates(secs, spy, days, up)
    return P.simulate(variant, dict(secs) | {spy.key: spy}, spy, days, cands, P.COST, fund=None, xexit=xexit), cands


def test_momentum_buys_the_top_20_at_the_next_open_after_a_month_end_and_only_then():
    secs, spy, days = _world()
    res, cands = _run(P.S20_VARIANTS[0], secs, spy, days)
    ends = S.month_ends(days)
    entries = {t.entry_day for t in res["trades"]} | {d for _k, _s, d, _st in res["open"]}
    assert entries and all(days[days.index(d) - 1] in ends for d in entries)
    first = min(d for d, v in cands["M"].items() if v)  # the first month-end with a year of history
    direct = sorted(secs, key=lambda k: -S.momentum(secs[k].c)[secs[k].index[first]])[: S.MOM_TOP]
    assert [k for _v, k in cands["M"][first]] == direct
    held = len(res["open"])
    assert 0 < held <= 20 and min(v for _, v in res["equity"]) > 0


def test_the_market_filter_sells_everything_and_buys_nothing_while_spy_is_under_its_line():
    secs, spy, days = _world(spy_up=False)
    res, cands = _run(P.S20_VARIANTS[0], secs, spy, days)
    assert not cands["M"] and not res["trades"] and not res["open"]
    res_nf, _ = _run(P.S20_VARIANTS[1], secs, spy, days)  # without the filter it still invests
    assert res_nf["open"]


def test_spy_trend_holds_spy_after_a_month_end_above_the_line_and_the_overlay_needs_all_five():
    secs, spy, days = _world()
    res, _ = _run(P.S20_VARIANTS[3], secs, spy, days)
    assert [k for k, _s, _d, st in res["open"]] == [spy.key]
    assert res["invested"][-1] > 0.95  # one slot = the whole account
    good = {"sharpe": 1.0, "max_drawdown": -0.2, "cagr": 0.14}
    spy_st = {"sharpe": 0.9, "max_drawdown": -0.33, "cagr": 0.15}
    assert P.verdict_overlay(good, [{"cagr": 0.1}, {"cagr": 0.1}], {"cagr": 0.12}, spy_st)["passed"]
    assert not P.verdict_overlay(good | {"max_drawdown": -0.3}, [{"cagr": 0.1}, {"cagr": 0.1}], {"cagr": 0.12}, spy_st)["passed"]


def test_a_large_keeps_only_signals_inside_the_days_top_500_by_dollar_volume():
    closes = [40 + 0.15 * i for i in range(300)]
    closes[-2] = closes[-3] * 0.97
    closes[-1] = closes[-2] * 0.97
    days = _days(303)
    big = {f"B{j:03d}": _sec(f"B{j:03d}", [50.0 + 0.01 * i for i in range(303)], days, vol=5e6) for j in range(S.LARGE_CAP_N)}
    small = _sec("SMALL", closes + [closes[-1]] * 3, days, vol=1e6)  # $20M+ a day, but under 500 bigger names
    spy = P.make_sec("SPY", "SPY", "ETF", days, _rows([100 + 0.1 * i for i in range(303)], vol=1e8), [])
    up = P.spy_up_map(spy)
    cands, _ = P.s20_candidates(big | {"SMALL": small}, spy, days, up)
    assert all(k != "SMALL" for lst in cands["AL"].values() for _p, k in lst)
    assert any(k == "SMALL" for lst in P.candidates("A", {"SMALL": small}, up).values() for _p, k in lst)


def test_the_committed_s20_summary_lists_every_variant_and_each_verdict_follows_its_checks():
    import json

    from marketlens.config import CONFIG_DIR

    d = json.loads((CONFIG_DIR / "strategy_results_s20.json").read_text(encoding="utf-8"))
    assert d["source"]["prereg"] == "PREREGISTRATION §20"
    assert set(d["variants"]) == {v.id for v in P.S20_VARIANTS}  # failures included
    for k, v in d["variants"].items():
        n = 5 if k == "T" else 6
        assert len(v["verdict"]["checks"]) == n and v["verdict"]["passed"] == all(v["verdict"]["checks"].values()), k
