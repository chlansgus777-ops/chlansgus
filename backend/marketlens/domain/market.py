"""Securities, quotes and bars plus price freshness rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from marketlens.domain.enums import DataMode, DataQuality, Exchange, TradingSession
from marketlens.domain.market_calendar import (
    classify_session,
    last_completed_session,
    session_close_utc,
)


@dataclass(frozen=True, slots=True)
class Security:
    ticker: str
    company_name: str
    exchange: Exchange
    sector: str
    industry: str
    market_cap: float | None
    is_etf: bool = False
    is_adr: bool = False
    country_of_incorporation: str = "US"
    currency: str = "USD"
    active: bool = True
    listed_at: date | None = None
    delisted_at: date | None = None
    cik: int | None = None  # SEC company identity (a ticker is only a label that can change or be reused)

    def was_listed_on(self, d: date) -> bool:
        if self.listed_at is not None and d < self.listed_at:
            return False
        return self.delisted_at is None or d < self.delisted_at


@dataclass(frozen=True, slots=True)
class Quote:
    ticker: str
    price: float | None
    timestamp: datetime
    session: TradingSession
    source: str
    mode: DataMode
    bid: float | None = None
    ask: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    previous_close: float | None = None
    volume: float | None = None
    premarket_price: float | None = None
    after_hours_price: float | None = None
    is_realtime: bool = True

    @property
    def spread_pct(self) -> float | None:
        if self.bid is None or self.ask is None or self.bid <= 0 or self.ask < self.bid:
            return None
        mid = (self.bid + self.ask) / 2
        return (self.ask - self.bid) / mid * 100


@dataclass(frozen=True, slots=True)
class Bar:
    """Daily OHLCV bar. ``day`` is the NY trading date."""

    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    realtime_max_age: timedelta = timedelta(minutes=2)
    delayed_max_age: timedelta = timedelta(minutes=20)


def assess_price_quality(quote: Quote | None, now: datetime, policy: FreshnessPolicy) -> DataQuality:
    """Decide whether a quote may be shown/used as *current*.

    - Market open (pre/regular/after): age must be within the delayed window, else STALE.
    - Market closed: the quote must be at/after the close of the last completed session, else STALE
      (a previous day's close must never be presented as the current price).
    """
    if quote is None or quote.price is None or quote.price <= 0:
        return DataQuality.MISSING
    if quote.timestamp > now + timedelta(minutes=1):
        # a timestamp from the future is corrupt data
        return DataQuality.CONFLICTING
    session_now = classify_session(now)
    age = now - quote.timestamp
    if session_now == TradingSession.CLOSED:
        last_close = session_close_utc(last_completed_session(now))
        # allow the closing print to be stamped slightly before the bell
        if quote.timestamp >= last_close - timedelta(minutes=1):
            return DataQuality.FRESH
        return DataQuality.STALE
    if age <= policy.realtime_max_age and quote.is_realtime:
        return DataQuality.FRESH
    if age <= policy.delayed_max_age:
        return DataQuality.DELAYED
    return DataQuality.STALE
