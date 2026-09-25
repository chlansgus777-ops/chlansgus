from datetime import date, datetime, timezone

from marketlens.domain.enums import TradingSession
from marketlens.domain.market_calendar import (
    add_trading_days, classify_session, early_closes, is_trading_day, last_completed_session,
    nyse_holidays, trading_days_between,
)

UTC = timezone.utc


def test_2026_holidays():
    h = nyse_holidays(2026)
    for d in [date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
              date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)]:
        assert d in h, d
    assert date(2026, 7, 4) not in h  # Saturday → observed Friday Jul 3


def test_new_year_on_saturday_not_observed_on_friday():
    assert date(2021, 12, 31) not in nyse_holidays(2022)
    assert is_trading_day(date(2021, 12, 31))


def test_early_close_day_after_thanksgiving():
    assert date(2026, 11, 27) in early_closes(2026)
    assert classify_session(datetime(2026, 11, 27, 18, 30, tzinfo=UTC)) == TradingSession.AFTER_HOURS  # 13:30 ET


def test_sessions_during_dst_and_standard_time():
    # Summer (EDT, UTC-4): 13:31Z = 09:31 ET
    assert classify_session(datetime(2026, 7, 15, 13, 31, tzinfo=UTC)) == TradingSession.REGULAR
    assert classify_session(datetime(2026, 7, 15, 13, 29, tzinfo=UTC)) == TradingSession.PREMARKET
    # Winter (EST, UTC-5): 13:31Z = 08:31 ET → premarket
    assert classify_session(datetime(2026, 1, 14, 13, 31, tzinfo=UTC)) == TradingSession.PREMARKET
    assert classify_session(datetime(2026, 1, 14, 14, 31, tzinfo=UTC)) == TradingSession.REGULAR
    assert classify_session(datetime(2026, 1, 14, 21, 30, tzinfo=UTC)) == TradingSession.AFTER_HOURS
    assert classify_session(datetime(2026, 1, 15, 2, 0, tzinfo=UTC)) == TradingSession.CLOSED


def test_dst_transition_days():
    # 2026-03-09 is the first trading day after the DST switch (Mar 8)
    assert classify_session(datetime(2026, 3, 9, 13, 30, tzinfo=UTC)) == TradingSession.REGULAR
    assert classify_session(datetime(2026, 3, 6, 13, 30, tzinfo=UTC)) == TradingSession.PREMARKET
    # 2026-11-02 first trading day after fall back (Nov 1)
    assert classify_session(datetime(2026, 11, 2, 14, 30, tzinfo=UTC)) == TradingSession.REGULAR
    assert classify_session(datetime(2026, 11, 2, 13, 45, tzinfo=UTC)) == TradingSession.PREMARKET


def test_weekend_and_holiday_closed():
    assert classify_session(datetime(2026, 9, 26, 15, 0, tzinfo=UTC)) == TradingSession.CLOSED
    assert classify_session(datetime(2026, 12, 25, 15, 0, tzinfo=UTC)) == TradingSession.CLOSED


def test_trading_day_arithmetic():
    assert add_trading_days(date(2026, 9, 25), 1) == date(2026, 9, 28)
    assert trading_days_between(date(2026, 9, 25), date(2026, 9, 28)) == 1
    assert trading_days_between(date(2026, 12, 24), date(2026, 12, 28)) == 1  # Christmas skipped
    assert add_trading_days(date(2026, 9, 28), -1) == date(2026, 9, 25)


def test_last_completed_session():
    assert last_completed_session(datetime(2026, 9, 25, 15, 0, tzinfo=UTC)) == date(2026, 9, 24)
    assert last_completed_session(datetime(2026, 9, 25, 21, 0, tzinfo=UTC)) == date(2026, 9, 25)
    assert last_completed_session(datetime(2026, 9, 27, 15, 0, tzinfo=UTC)) == date(2026, 9, 25)


def test_naive_datetime_rejected():
    import pytest

    with pytest.raises(ValueError):
        classify_session(datetime(2026, 9, 25, 15, 0))
