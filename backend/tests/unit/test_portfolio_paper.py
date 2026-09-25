from datetime import date, datetime, timedelta, timezone

import pytest

from marketlens.domain.enums import ExitReason, SizeClass
from marketlens.domain.market import Bar
from marketlens.domain.paper import PaperConfig, PaperSignal, compute_metrics, first_tradable_day, max_drawdown, simulate
from marketlens.domain.portfolio import CandidateProfile, Holding, Portfolio, review_candidate

UTC = timezone.utc


def test_sector_concentration_limits_size():
    pf = Portfolio((Holding("A", 100, 100, "Technology"), Holding("B", 100, 100, "Technology"), Holding("C", 50, 100, "Energy")), 20_000)
    r = review_candidate(pf, {"A": 100, "B": 100, "C": 100}, CandidateProfile("D", "Technology", (), 0.0), {})
    assert r.size_cap == SizeClass.WATCH and any("섹터" in w for w in r.warnings)


def _dated(values: list[float], start: date = date(2026, 1, 5)) -> dict[date, float]:
    from marketlens.domain.market_calendar import add_trading_days

    return {add_trading_days(start, i): v for i, v in enumerate(values)}


def test_high_correlation_limits_size():
    rets = _dated([0.01 * ((i % 5) - 2) for i in range(60)])
    pf = Portfolio((Holding("A", 10, 100, "Energy"),), 100_000)
    r = review_candidate(pf, {"A": 100}, CandidateProfile("B", "Technology", (), 0.0, rets), {"A": rets})
    assert r.size_cap == SizeClass.HALF and r.max_correlation[0] == "A" and r.max_correlation[1] > 0.99


def test_correlation_uses_date_alignment_not_positions():
    """Regression (거래일 미정렬 correlation 정정): one series misses a bar in the middle. Pairing by array
    position shifts every later pair by a day and destroys the correlation; pairing by date keeps it."""
    from marketlens.domain.portfolio import aligned_pearson, pearson

    import random

    rnd = random.Random(7)
    a = _dated([rnd.gauss(0, 0.02) for _ in range(80)])
    days = sorted(a)
    c = {d: v for d, v in a.items() if d != days[70]}  # identical returns, one missing session near the end
    positional = pearson(list(a.values()), list(c.values()))  # old behaviour: pair the last n values
    aligned = aligned_pearson(a, c)
    assert aligned is not None and aligned > 0.999
    assert positional is not None and positional < 0.5


def test_existing_position_is_not_correlated_with_itself():
    rets = _dated([0.01 * ((i % 5) - 2) for i in range(60)])
    pf = Portfolio((Holding("A", 10, 100, "Energy"),), 100_000)
    r = review_candidate(pf, {"A": 100}, CandidateProfile("A", "Energy", (), 0.0, rets), {"A": rets})
    assert r.max_correlation is None and r.size_cap == SizeClass.FULL


def test_theme_overlap():
    pf = Portfolio((Holding("NVDA", 400, 100, "Technology", ("AI",)),), 60_000)
    r = review_candidate(pf, {"NVDA": 100}, CandidateProfile("TSM", "Semis", ("AI",), 0.0), {})
    assert r.size_cap == SizeClass.SMALL


def bars(start: date, rows: list[tuple[float, float, float, float]]) -> list[Bar]:
    out = []
    d = start
    for o, h, l_, c in rows:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append(Bar(d, o, h, l_, c, 1e6))
        d += timedelta(days=1)
    return out


def sig(ts: datetime) -> PaperSignal:
    return PaperSignal("X", ts, "BUY", 85, 80, stop=95, target1=110, target2=120, thesis="t", model_version="v", regime="r", sector="s")


def test_entry_is_next_tradable_bar_open_not_past_price():
    ts = datetime(2026, 9, 21, 18, 0, tzinfo=UTC)  # Monday 14:00 ET, market open → next open is Tuesday
    assert first_tradable_day(ts) == date(2026, 9, 22)
    b = bars(date(2026, 9, 21), [(50, 51, 49, 50), (100, 101, 99, 100), (100, 101, 99, 100)])
    r = simulate(sig(ts), b, PaperConfig(slippage_bps=10, default_half_spread_bps=0))
    assert r.entry.day == date(2026, 9, 22)
    assert r.entry.price == pytest.approx(100 * 1.001)


def test_premarket_recommendation_enters_same_day_open():
    ts = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)  # 08:00 ET
    assert first_tradable_day(ts) == date(2026, 9, 22)


def test_gap_below_stop_fills_at_open():
    ts = datetime(2026, 9, 21, 18, 0, tzinfo=UTC)
    b = bars(date(2026, 9, 22), [(100, 101, 99, 100), (90, 92, 88, 91)])
    r = simulate(sig(ts), b, PaperConfig(slippage_bps=0, default_half_spread_bps=0))
    assert r.exit_reasons == (ExitReason.STOP,) and r.exits[0].price == 90
    assert r.return_pct == pytest.approx(-0.1)


def test_targets_partial_then_full():
    ts = datetime(2026, 9, 21, 18, 0, tzinfo=UTC)
    b = bars(date(2026, 9, 22), [(100, 101, 99, 100), (105, 111, 104, 110), (112, 121, 111, 120)])
    r = simulate(sig(ts), b, PaperConfig(slippage_bps=0, default_half_spread_bps=0))
    assert r.exit_reasons == (ExitReason.TARGET_1, ExitReason.TARGET_2)
    assert r.return_pct == pytest.approx(0.15)
    # MFE only counts price action up to the exit fill: the position was gone at 120, the 121 high came after
    assert r.mfe_pct == pytest.approx(0.20)


def test_time_exit_and_open_position_mark():
    ts = datetime(2026, 9, 21, 18, 0, tzinfo=UTC)
    b = bars(date(2026, 9, 22), [(100, 101, 99, 100)] * 5)
    r = simulate(sig(ts), b, PaperConfig(max_holding_days=3, slippage_bps=0, default_half_spread_bps=0))
    assert r.exit_reasons == (ExitReason.TIME_EXIT,)
    r2 = simulate(sig(ts), b[:2], PaperConfig(slippage_bps=0, default_half_spread_bps=0))
    assert r2.open


def test_downgrade_event_exits_next_open():
    ts = datetime(2026, 9, 21, 18, 0, tzinfo=UTC)
    b = bars(date(2026, 9, 22), [(100, 101, 99, 100), (101, 102, 100, 101), (103, 104, 102, 103)])
    after_close = datetime(2026, 9, 22, 21, 0, tzinfo=UTC)  # 17:00 ET on b[0] → next open is b[1]
    r = simulate(sig(ts), b, PaperConfig(slippage_bps=0, default_half_spread_bps=0), [(after_close, ExitReason.RECOMMENDATION_DOWNGRADE)])
    assert r.exit_reasons == (ExitReason.RECOMMENDATION_DOWNGRADE,) and r.exits[0].day == b[1].day and r.exits[0].price == 101


def test_afternoon_exit_signal_is_not_filled_at_that_mornings_open():
    """Regression: a 14:00 ET downgrade on day D must fill at D+1's open, never at D's (earlier) open."""
    ts = datetime(2026, 9, 21, 18, 0, tzinfo=UTC)
    b = bars(date(2026, 9, 22), [(100, 101, 99, 100), (101, 102, 100, 101), (103, 104, 102, 103)])
    afternoon = datetime(2026, 9, 23, 18, 0, tzinfo=UTC)  # 14:00 ET on b[1] (market open)
    r = simulate(sig(ts), b, PaperConfig(slippage_bps=0, default_half_spread_bps=0), [(afternoon, ExitReason.RECOMMENDATION_DOWNGRADE)])
    assert r.exits[0].day == b[2].day and r.exits[0].price == 103
    assert r.exits[0].day != b[1].day


def test_as_of_hides_future_bars():
    ts = datetime(2026, 9, 21, 18, 0, tzinfo=UTC)
    b = bars(date(2026, 9, 22), [(100, 101, 99, 100), (105, 111, 104, 110)])
    r = simulate(sig(ts), b, PaperConfig(), as_of=b[0].day)
    assert r.open and ExitReason.TARGET_1 not in r.exit_reasons


def test_metrics():
    ts = datetime(2026, 9, 21, 18, 0, tzinfo=UTC)
    win = simulate(sig(ts), bars(date(2026, 9, 22), [(100, 101, 99, 100), (105, 111, 104, 110), (112, 121, 111, 120)]), PaperConfig(slippage_bps=0, default_half_spread_bps=0))
    loss = simulate(sig(ts), bars(date(2026, 9, 22), [(100, 101, 99, 100), (90, 92, 88, 91)]), PaperConfig(slippage_bps=0, default_half_spread_bps=0))
    m = compute_metrics([win, loss], [0.01, 0.01])
    assert m.win_rate == 0.5 and m.profit_factor == pytest.approx(1.5) and m.expectancy == pytest.approx(0.025)
    assert m.excess_vs_benchmark == pytest.approx(0.015)
    assert max_drawdown([1, 1.2, 0.9, 1.1]) == pytest.approx(-0.25)
