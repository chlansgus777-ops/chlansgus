"""Backtest stage 0: ticker → company on a date, and the weekly analysis times."""

from datetime import date

from marketlens.backtest.identity import TickerRecord, build_ticker_map, lookup, weekly_times


def test_a_reused_ticker_maps_to_each_company_in_its_own_time():
    recs = [TickerRecord("ABC", False, 111, "CS", "Old Co", "XNYS", date(2025, 3, 10)),
            TickerRecord("ABC", True, 222, "CS", "New Co", "XNAS", None),
            TickerRecord("OLD", False, 333, "CS", "Renamed Co", "XNYS", date(2025, 6, 2)),
            TickerRecord("NEW", True, 333, "CS", "Renamed Co", "XNYS", None),
            TickerRecord("NOCIK", True, None, "CS", "No Cik", "XNAS", None)]
    ivs = build_ticker_map(recs, {"NOCIK": 444})
    abc = [i for i in ivs if i.ticker == "ABC"]
    assert lookup(abc, date(2025, 3, 7)).cik == 111 and lookup(abc, date(2025, 3, 10)).cik == 222
    old = [i for i in ivs if i.ticker == "OLD"]
    assert lookup(old, date(2025, 6, 1)).cik == 333 and lookup(old, date(2025, 6, 2)) is None
    assert lookup([i for i in ivs if i.ticker == "NEW"], date(2025, 7, 1)).cik == 333  # a rename keeps the company
    assert lookup([i for i in ivs if i.ticker == "NOCIK"], date(2025, 7, 1)).cik == 444  # SEC's current map fills a gap


def test_weekly_times_are_the_last_session_of_each_week_at_20_ny():
    ts = weekly_times(date(2025, 11, 24), date(2025, 12, 7))  # Thanksgiving week: Friday 28th is a half day
    assert [t.date() for t in ts] == [date(2025, 11, 28), date(2025, 12, 5)]
    assert all(t.hour == 20 and str(t.tzinfo) == "America/New_York" for t in ts)
