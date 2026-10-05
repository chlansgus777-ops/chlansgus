"""Strategy rules A-1.0 / C-1.0 (domain/strategies.py) and the daily portfolio engine (backtest/portfolio.py) on
synthetic bars — rule checks only, never a performance figure."""

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


def _rows(closes, vol=2e6, opens=None, highs=None, lows=None):
    o = opens or closes
    h = highs or [max(a, b) * 1.002 for a, b in zip(o, closes)]
    lo = lows or [min(a, b) * 0.998 for a, b in zip(o, closes)]
    v = vol if isinstance(vol, list) else [vol] * len(closes)
    return list(zip(o, h, lo, closes, v))


def _ind(closes, **kw):
    days = _days(len(closes))
    o, h, lo, c, v, raw, _f = S.split_adjust(days, _rows(closes, **kw), [])
    return S.indicators(o, h, lo, c, v, raw)


def _uptrend_two_down(n=300):
    closes = [40 + 0.15 * i for i in range(n)]
    closes[-2] = closes[-3] * 0.97
    closes[-1] = closes[-2] * 0.97
    return closes


def test_a_needs_every_condition_and_two_falling_closes():
    ind = _ind(_uptrend_two_down(), vol=2e6)
    sig = S.signals("A", ind)
    assert sig[-1] and not sig[-3]
    one_down = _uptrend_two_down()
    one_down[-2] = one_down[-3] * 1.001  # only one falling close
    assert not S.signals("A", _ind(one_down, vol=2e6))[-1]
    # thin trading: outside the universe whatever the pattern
    assert not S.signals("A", _ind(_uptrend_two_down(), vol=10_000))[-1]


def test_c_breakout_reference_and_average_volume_exclude_today():
    n = 300
    closes = [50.0 + 0.01 * (i % 5) for i in range(n)]
    vol = [1e6] * n
    closes[-1] = 53.0
    vol[-1] = 1.6e6
    ind = _ind(closes, vol=vol)
    up = np.ones(n, dtype=bool)
    assert S.signals("C", ind, up)[-1]
    # the reference high is the prior 20 sessions (today's own high is never the bar to beat)
    assert ind["high20_prior"][-1] < 53.0
    vol[-1] = 1.4e6  # below 1.5 × the prior average
    assert not S.signals("C", _ind(closes, vol=vol), up)[-1]
    vol[-1] = 1.6e6
    assert not S.signals("C", _ind(closes, vol=vol), np.zeros(n, dtype=bool))[-1]  # SPY under its 200-day line


def test_exits_a_rule_or_time_and_c_below_the_prior_10_day_low():
    ind = _ind(_uptrend_two_down() + [1.0] * 0)
    i = len(ind["close"]) - 1
    assert S.exit_due("A", ind, i, 3) is None  # still under the 5-day line
    assert S.exit_due("A", ind, i, 10) == "time"
    up = _ind([40 + 0.15 * k for k in range(300)])
    assert S.exit_due("A", up, 299, 10) == "rule"  # both: the rule exit is recorded
    c = [50.0] * 299 + [45.0]
    assert S.exit_due("C", _ind(c), 299, 5) == "rule"
    assert S.exit_due("C", _ind([50.0] * 300), 299, 5) is None


def test_signals_never_read_a_later_bar():
    rng = np.random.default_rng(3)
    closes = list(60 * np.cumprod(1 + rng.normal(0.0005, 0.02, 420)))
    vol = list(rng.uniform(1e6, 3e6, 420))
    a = _ind(closes, vol=vol)
    b = _ind(closes[:350] + [x * 2 for x in closes[350:]], vol=vol[:350] + [x * 5 for x in vol[350:]])
    up = np.ones(420, dtype=bool)
    for st in ("A", "C"):
        assert np.array_equal(S.signals(st, a, up)[:350], S.signals(st, b, up)[:350]), st


def test_data_holds_say_why():
    assert S.data_holds("A", 100, True, 300)[0].startswith("일봉 100거래일")
    assert any("거래량" in x for x in S.data_holds("C", 300, False, 300))
    assert any("SPY" in x for x in S.data_holds("C", 300, True, 50))
    assert S.data_holds("C", 300, True, 300) == []


# ---------------------------------------------------------------------- the account
def _sec(key, closes, days, sector="Tech", vol=2e6, opens=None, lows=None, dividends=()):
    return P.make_sec(key, key, sector, days, _rows(closes, vol=vol, opens=opens, lows=lows), [], dividends)


def _spy(days):
    return P.make_sec("SPY", "SPY", "ETF", days, _rows([100 + 0.1 * i for i in range(len(days))], vol=1e8), [])


def _run(variant, secs, days, cost=0.0015, fund=None):
    spy = _spy(days)
    up = P.spy_up_map(spy)
    cands = {st: P.candidates(st, secs, up) for st in ("A", "C")}
    return P.simulate(variant, secs, spy, days, cands, cost, fund=fund)


def test_entry_is_the_next_open_never_the_signal_close_and_costs_count_twice():
    closes = _uptrend_two_down() + [0.0] * 3
    base = closes[-4]
    closes[-3:] = [base * 0.99, base * 1.05, base * 1.06]
    opens = closes[:]
    opens[-3] = base * 0.985
    days = _days(len(closes))
    res = _run(P.VARIANTS[0], {"X": _sec("X", closes, days, opens=opens)}, days)
    t = res["trades"][0] if res["trades"] else None
    sig_day = len(closes) - 4
    assert t is not None and t.entry_day == days[sig_day + 1] and t.entry_px == opens[sig_day + 1]
    assert abs(t.ret - (t.exit_px * (1 - 0.0015) / (t.entry_px * (1 + 0.0015)) - 1)) < 1e-9


def test_a_halted_next_day_cancels_the_entry_and_is_recorded():
    closes = _uptrend_two_down()
    days = _days(len(closes) + 3)
    sec = P.make_sec("X", "X", "Tech", days[: len(closes)] + days[len(closes) + 1:], _rows(closes + [closes[-1]] * 2), [])
    res = _run(P.VARIANTS[0], {"X": sec}, days)
    assert res["missed"] >= 1 and not any(t.entry_day == days[len(closes)] for t in res["trades"])


def test_a_stop_gap_fills_at_the_open_and_money_is_never_used_twice():
    closes = _uptrend_two_down()
    n = len(closes)
    days = _days(n + 3)
    c2 = closes + [closes[-1], closes[-1] * 0.6, closes[-1] * 0.6]  # entry at a normal open, a gap down the day after
    opens = c2[:]
    sec = _sec("X", c2, days, opens=opens)
    res = _run(P.VARIANTS[3], {"X": sec}, days)  # A + stop
    t = res["trades"][0]
    assert t.reason == "stop" and t.exit_px == opens[n + 1]
    # 15 names all signalling the same day: at most 10 positions, 3 per sector, cash never negative
    secs = {f"S{k}": _sec(f"S{k}", closes + [closes[-1]] * 3, days, sector=f"sec{k % 4}") for k in range(15)}
    r2 = _run(P.VARIANTS[0], secs, days)
    held = [(t.key, t.sector) for t in r2["trades"]] + [(k, sec) for k, sec, _d, _s in r2["open"]]
    assert min(v for _, v in r2["equity"]) > 0
    assert 0 < len({k for k, _ in held}) <= 10
    per_sector: dict[str, int] = {}
    for _k, sec in held:
        per_sector[sec] = per_sector.get(sec, 0) + 1
    assert max(per_sector.values()) <= P.SECTOR_MAX


def test_a_dividend_is_paid_only_when_held_before_the_ex_date_and_spy_includes_its_own():
    days = _days(320)
    spy = P.make_sec("SPY", "SPY", "ETF", days, _rows([100.0] * 320, vol=1e8), [], [(days[-10], 1.0)])
    tr = P.spy_total_return(spy, days[0], days[-1])
    assert abs(tr[-1][1] / tr[0][1] - 1.01) < 1e-9  # flat price, one 1 % dividend reinvested


def test_the_combination_never_holds_one_name_twice():
    closes = _uptrend_two_down()
    days = _days(len(closes) + 3)
    secs = {"X": _sec("X", closes + [closes[-1]] * 3, days)}
    res = _run(P.VARIANTS[2], secs, days)
    opens = [t for t in res["trades"] if t.ticker == "X"]
    assert len({t.entry_day for t in opens}) == len(opens)


def test_the_verdict_needs_all_six():
    full = {"cagr": 0.12, "sharpe": 1.1, "trades": {"trades": 150, "top10_trade_share": 0.3}}
    ok = P.verdict(full, [{"cagr": 0.05}, {"cagr": 0.02}], {"trades": {"expectancy": 0.001}}, {"sharpe": 1.0}, {"cagr": 0.08})
    assert ok["passed"]
    bad = P.verdict(full | {"trades": {"trades": 150, "top10_trade_share": 0.7}}, [{"cagr": 0.05}, {"cagr": 0.02}], {"trades": {"expectancy": 0.001}}, {"sharpe": 1.0}, {"cagr": 0.08})
    assert not bad["passed"] and not bad["checks"]["top10_trades_under_half"]
