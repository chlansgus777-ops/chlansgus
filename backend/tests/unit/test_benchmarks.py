"""Yardsticks of the routine (backtest/benchmarks.py) on synthetic bars — rule checks only, never a performance figure."""

from datetime import date

from marketlens.backtest import benchmarks as B
from marketlens.backtest import portfolio as P
from marketlens.domain import strategies as S

from .test_strategies_s20 import _days, _world


def test_deposit_accrues_the_latest_published_rate_per_calendar_day():
    cal = [date(2020, 1, 3), date(2020, 1, 6), date(2020, 1, 7)]
    eq = B.deposit_curve(cal, [(date(2020, 1, 1), 0.0365), (date(2020, 1, 6), 0.0)])
    assert eq[0][1] == P.START_CASH
    assert abs(eq[1][1] - P.START_CASH * (1 + 0.0365 * 3 / 365)) < 1e-9  # Fri → Mon: three days at the Friday rate
    assert eq[2][1] == eq[1][1]  # the Monday rate (0 %) for Mon → Tue


def test_random_buying_uses_the_routines_universe_and_schedule_and_differs_by_seed():
    secs, spy, days = _world(names=60)
    elig = B.eligible_by_month(secs, days)
    ends = S.month_ends(days)
    assert set(elig) == ends
    a, xa = B.random_candidates(elig, 0)
    b, _ = B.random_candidates(elig, 1)
    again, _ = B.random_candidates(elig, 0)
    assert a == again and a != b
    for d, lst in a["RND"].items():
        assert len(lst) <= S.MOM_TOP and {k for _v, k in lst} <= set(elig[d])
    d = next(d for d, v in a["RND"].items() if v)
    held = a["RND"][d][0][1]
    assert xa("RND", held, d) is None and xa("RND", held, days[days.index(d) - 1]) is None
    v = P.Variant("RND", "무작위", (P.Sleeve("RND", S.MOM_TOP),), sector_max=5)
    res = P.simulate(v, secs, spy, days, a, P.COST, xexit=xa)
    assert res["open"] and min(x for _, x in res["equity"]) > 0


def test_summaries_name_the_worst_year_and_the_deepest_fall():
    days = _days(300)
    eq = [(d, 100.0 + (i if i < 100 else 200 - i if i < 150 else i - 100)) for i, d in enumerate(days)]
    w = B.worst_12m(eq)
    dd = B.drawdown_span(eq)
    assert w is not None and dd is not None
    assert dd["peak"] == days[100].isoformat() and dd["bottom"] == days[150].isoformat() and dd["recovered"] is not None
    assert B.monthly(eq)[-1][1] == round(eq[-1][1] / eq[0][1] * 100, 2)
