"""A-grade acceptance: recommendation freshness and the exchange calendar."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from marketlens.domain.freshness import PlanCheck, recommendation_freshness
from marketlens.domain.market_calendar import add_trading_days, is_trading_day, market_active_between, trading_days_between

NY = ZoneInfo("America/New_York")
PLAN = PlanCheck(rec_price=100.0, max_buy=101.0, stop=95.0, target1=112.0, min_rr=2.0, bullish=True)


def ny(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=NY)


D = date(2026, 9, 23)  # a Wednesday


def test_six_hour_old_intraday_buy_is_not_current():
    """Audit P0: 09:35 FRESH recommendation judged at 15:55 the same day was CURRENT/actionable."""
    st = recommendation_freshness(ny(D, 9, 35), "FRESH", ny(D, 15, 55), plan=PLAN)
    assert st.status == "NEEDS_REVALIDATION" and not st.actionable


def test_current_quote_can_revalidate_or_invalidate():
    now = ny(D, 15, 55)
    ok = recommendation_freshness(ny(D, 9, 35), "FRESH", now, plan=PLAN, quote_price=100.5, quote_ts=now - timedelta(minutes=1))
    assert ok.status == "CURRENT" and ok.actionable and ok.revalidated_price == 100.5
    above = recommendation_freshness(ny(D, 9, 35), "FRESH", now, plan=PLAN, quote_price=101.8, quote_ts=now)
    assert above.status == "PLAN_INVALIDATED" and any("최대 매수가" in p for p in above.problems)
    below_stop = recommendation_freshness(ny(D, 9, 35), "FRESH", now, plan=PLAN, quote_price=94.0, quote_ts=now)
    assert below_stop.status == "PLAN_INVALIDATED" and not below_stop.actionable
    thin_rr = recommendation_freshness(ny(D, 9, 35), "FRESH", now, plan=PlanCheck(100.0, 105.0, 99.0, 102.0, 2.0, True), quote_price=100.2, quote_ts=now)
    assert thin_rr.status == "PLAN_INVALIDATED" and any("손익비" in p for p in thin_rr.problems)


def test_old_quote_cannot_revalidate():
    now = ny(D, 15, 55)
    st = recommendation_freshness(ny(D, 9, 35), "FRESH", now, plan=PLAN, quote_price=100.0, quote_ts=now - timedelta(hours=2))
    assert st.status == "NEEDS_REVALIDATION"


def test_young_or_market_closed_recommendation_stays_current():
    assert recommendation_freshness(ny(D, 9, 35), "FRESH", ny(D, 9, 50), plan=PLAN).status == "CURRENT"
    # stricter since evaluation 3 (N11): 45 minutes of trading without a newer quote is no longer "young enough"
    assert recommendation_freshness(ny(D, 9, 35), "FRESH", ny(D, 10, 20), plan=PLAN).status == "NEEDS_REVALIDATION"
    fri, sat = date(2026, 9, 25), date(2026, 9, 26)
    assert recommendation_freshness(ny(fri, 21, 0), "FRESH", ny(sat, 12, 0), plan=PLAN).status == "CURRENT"  # nothing traded


def test_major_new_issue_requires_reanalysis():
    st = recommendation_freshness(ny(D, 9, 35), "FRESH", ny(D, 9, 50), plan=PLAN, new_major_events=("수출 규제 강화",))
    assert st.status == "NEEDS_REVALIDATION"


def test_special_closures_are_not_trading_days():
    """Audit P1: 2025-01-09 (President Carter day of mourning) was treated as a trading day."""
    for d in (date(2025, 1, 9), date(2018, 12, 5), date(2012, 10, 29), date(2012, 10, 30), date(2007, 1, 2), date(2001, 9, 11)):
        assert not is_trading_day(d), d
    assert add_trading_days(date(2025, 1, 8), 1) == date(2025, 1, 10)
    assert trading_days_between(date(2025, 1, 8), date(2025, 1, 10)) == 1


def test_market_activity_window_uses_the_calendar():
    assert market_active_between(ny(D, 9, 0), ny(D, 9, 10))
    assert not market_active_between(ny(date(2025, 1, 9), 9, 0), ny(date(2025, 1, 9), 16, 0))  # special closure
    assert not market_active_between(ny(D, 20, 30), ny(D + timedelta(days=1), 3, 30))  # overnight gap
