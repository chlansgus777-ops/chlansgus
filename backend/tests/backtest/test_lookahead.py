"""Look-ahead / future-leakage / timestamp-boundary tests."""

from datetime import date, datetime, timezone

from marketlens.application.evaluation_service import rec_session_day
from marketlens.domain.evaluation import OutcomeSample, factor_ic, forward_return, is_mature
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import add_trading_days
from marketlens.domain.paper import PaperSignal, first_tradable_day, simulate

UTC = timezone.utc


def test_rec_base_session_boundary():
    # 15:59 ET → same-day close is after the rec; 16:01 ET → next session
    assert rec_session_day(datetime(2026, 9, 24, 19, 59, tzinfo=UTC)) == date(2026, 9, 24)
    assert rec_session_day(datetime(2026, 9, 24, 20, 1, tzinfo=UTC)) == date(2026, 9, 25)
    assert rec_session_day(datetime(2026, 9, 26, 15, 0, tzinfo=UTC)) == date(2026, 9, 28)  # Saturday


def test_paper_entry_boundary_at_open():
    assert first_tradable_day(datetime(2026, 9, 24, 13, 29, tzinfo=UTC)) == date(2026, 9, 24)  # 09:29 ET
    assert first_tradable_day(datetime(2026, 9, 24, 13, 30, tzinfo=UTC)) == date(2026, 9, 25)  # 09:30 ET → next open


def test_paper_never_uses_bars_before_recommendation():
    bars = [Bar(date(2026, 9, 21), 10, 200, 1, 150, 1), Bar(date(2026, 9, 22), 100, 101, 99, 100, 1)]
    s = PaperSignal("X", datetime(2026, 9, 21, 18, 0, tzinfo=UTC), "BUY", 80, 80, 95, 110, 120, "", "v", "r", "s")
    r = simulate(s, bars)
    assert r.entry.day == date(2026, 9, 22) and r.mfe_pct < 0.02


def test_outcomes_not_available_before_horizon():
    d = date(2026, 9, 1)
    bars = [Bar(add_trading_days(d, i), 1, 1, 1, 1 + i, 1) for i in range(80)]
    for h in (1, 5, 20, 60):
        assert forward_return(bars, d, h, add_trading_days(d, h - 1)) is None
        assert forward_return(bars, d, h, add_trading_days(d, h)) is not None


def test_ic_dataset_excludes_unmatured_even_if_return_present():
    d = date(2026, 9, 20)
    s = [OutcomeSample(f"T{i}", d, {"fundamental": float(i)}, {20: float(i)}) for i in range(50)]
    assert factor_ic(s, "fundamental", 20, as_of=date(2026, 9, 25)).samples == 0
    assert not is_mature(d, 20, date(2026, 9, 25))
