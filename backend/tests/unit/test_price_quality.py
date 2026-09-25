from datetime import datetime, timedelta, timezone

from marketlens.domain.enums import DataMode, DataQuality, TradingSession
from marketlens.domain.market import FreshnessPolicy, Quote, assess_price_quality
from marketlens.domain.market_calendar import session_close_utc

UTC = timezone.utc
POL = FreshnessPolicy()
NOW = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)  # regular session


def q(ts, price=100.0, rt=True):
    return Quote("X", price, ts, TradingSession.REGULAR, "t", DataMode.LIVE, is_realtime=rt)


def test_fresh_realtime():
    assert assess_price_quality(q(NOW - timedelta(seconds=30)), NOW, POL) == DataQuality.FRESH


def test_delayed_quote():
    assert assess_price_quality(q(NOW - timedelta(minutes=15)), NOW, POL) == DataQuality.DELAYED


def test_old_quote_is_stale_during_session():
    assert assess_price_quality(q(NOW - timedelta(hours=3)), NOW, POL) == DataQuality.STALE


def test_previous_close_is_stale_when_market_open():
    prev_close = session_close_utc(datetime(2026, 9, 24).date())
    assert assess_price_quality(q(prev_close), NOW, POL) == DataQuality.STALE


def test_closed_market_uses_last_close_as_fresh_but_older_close_stale():
    sat = datetime(2026, 9, 26, 15, 0, tzinfo=UTC)
    fri_close = session_close_utc(datetime(2026, 9, 25).date())
    thu_close = session_close_utc(datetime(2026, 9, 24).date())
    assert assess_price_quality(q(fri_close), sat, POL) == DataQuality.FRESH
    assert assess_price_quality(q(thu_close), sat, POL) == DataQuality.STALE


def test_missing_and_future():
    assert assess_price_quality(None, NOW, POL) == DataQuality.MISSING
    assert assess_price_quality(q(NOW, price=0), NOW, POL) == DataQuality.MISSING
    assert assess_price_quality(q(NOW + timedelta(hours=1)), NOW, POL) == DataQuality.CONFLICTING
