"""NYSE trading calendar and session classification.

All timestamps are timezone-aware. Storage is UTC; market logic runs in America/New_York so DST is
handled by zoneinfo. Holidays are computed by rule (NYSE holiday rules incl. weekend observance).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from marketlens.domain.enums import TradingSession

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

PREMARKET_OPEN = time(4, 0)
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)
AFTER_HOURS_CLOSE = time(20, 0)


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


# Unscheduled full-day NYSE/Nasdaq closures (national days of mourning, emergencies). These are announced
# ad hoc by the exchange and cannot be derived from a rule — add new ones here when NYSE announces them.
# Source: NYSE holiday & closure notices (nyse.com/markets/hours-calendars, NYSE press releases).
SPECIAL_CLOSURES: frozenset[date] = frozenset({
    date(1994, 4, 27),  # President Nixon national day of mourning
    date(2001, 9, 11), date(2001, 9, 12), date(2001, 9, 13), date(2001, 9, 14),  # September 11 attacks
    date(2004, 6, 11),  # President Reagan national day of mourning
    date(2007, 1, 2),  # President Ford national day of mourning
    date(2012, 10, 29), date(2012, 10, 30),  # Hurricane Sandy
    date(2018, 12, 5),  # President George H. W. Bush national day of mourning
    date(2025, 1, 9),  # President Carter national day of mourning
})


@lru_cache(maxsize=64)
def nyse_holidays(year: int) -> frozenset[date]:
    hs: set[date] = {d for d in SPECIAL_CLOSURES if d.year == year}
    ny = date(year, 1, 1)
    # NYSE does not observe New Year's on the prior Friday (Dec 31) when Jan 1 is a Saturday.
    if ny.weekday() == 6:
        hs.add(ny + timedelta(days=1))
    elif ny.weekday() != 5:
        hs.add(ny)
    hs.add(_nth_weekday(year, 1, 0, 3))  # MLK
    hs.add(_nth_weekday(year, 2, 0, 3))  # Presidents
    hs.add(_easter(year) - timedelta(days=2))  # Good Friday
    hs.add(_last_weekday(year, 5, 0))  # Memorial
    if year >= 2022:
        hs.add(_observed(date(year, 6, 19)))  # Juneteenth
    hs.add(_observed(date(year, 7, 4)))
    hs.add(_nth_weekday(year, 9, 0, 1))  # Labor
    hs.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving
    hs.add(_observed(date(year, 12, 25)))
    return frozenset(hs)


@lru_cache(maxsize=64)
def early_closes(year: int) -> frozenset[date]:
    days: set[date] = set()
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    days.add(thanksgiving + timedelta(days=1))
    xmas_eve = date(year, 12, 24)
    if xmas_eve.weekday() < 5 and xmas_eve not in nyse_holidays(year):
        days.add(xmas_eve)
    jul3 = date(year, 7, 3)
    if jul3.weekday() < 5 and jul3 not in nyse_holidays(year):
        days.add(jul3)
    return frozenset(days)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def regular_close_time(d: date) -> time:
    return EARLY_CLOSE if d in early_closes(d.year) else REGULAR_CLOSE


def to_ny(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("naive datetime not allowed; store and pass timezone-aware timestamps")
    return ts.astimezone(NY)


def classify_session(ts: datetime) -> TradingSession:
    local = to_ny(ts)
    d = local.date()
    if not is_trading_day(d):
        return TradingSession.CLOSED
    t = local.time()
    close = regular_close_time(d)
    if PREMARKET_OPEN <= t < REGULAR_OPEN:
        return TradingSession.PREMARKET
    if REGULAR_OPEN <= t < close:
        return TradingSession.REGULAR
    if close <= t < AFTER_HOURS_CLOSE and close == REGULAR_CLOSE:
        return TradingSession.AFTER_HOURS
    if close == EARLY_CLOSE and close <= t < time(17, 0):
        return TradingSession.AFTER_HOURS
    return TradingSession.CLOSED


def next_trading_day(d: date) -> date:
    n = d + timedelta(days=1)
    while not is_trading_day(n):
        n += timedelta(days=1)
    return n


def previous_trading_day(d: date) -> date:
    p = d - timedelta(days=1)
    while not is_trading_day(p):
        p -= timedelta(days=1)
    return p


def add_trading_days(d: date, n: int) -> date:
    cur = d
    step = 1 if n >= 0 else -1
    for _ in range(abs(n)):
        cur = next_trading_day(cur) if step > 0 else previous_trading_day(cur)
    return cur


def trading_days_between(start: date, end: date) -> int:
    """Number of trading sessions in (start, end]."""
    if end <= start:
        return 0
    count = 0
    cur = start
    while cur < end:
        cur += timedelta(days=1)
        if is_trading_day(cur):
            count += 1
    return count


def session_close_utc(d: date) -> datetime:
    return datetime.combine(d, regular_close_time(d), tzinfo=NY).astimezone(UTC)


EXTENDED_OPEN = time(4, 0)


def market_active_between(start: datetime, end: datetime) -> bool:
    """True when any trading session (pre-market 04:00 → after-hours close, ET) overlaps [start, end]:
    prices could have moved in between. Weekends, holidays and overnight gaps return False."""
    if end <= start:
        return False
    a, b = to_ny(start), to_ny(end)
    d = a.date()
    while d <= b.date():
        if is_trading_day(d):
            close = time(17, 0) if regular_close_time(d) == EARLY_CLOSE else AFTER_HOURS_CLOSE
            ws, we = datetime.combine(d, EXTENDED_OPEN, tzinfo=NY), datetime.combine(d, close, tzinfo=NY)
            if max(ws, a) < min(we, b):
                return True
        d += timedelta(days=1)
    return False


def last_completed_session(ts: datetime) -> date:
    """The most recent trading day whose regular session has closed at ``ts``."""
    local = to_ny(ts)
    d = local.date()
    if is_trading_day(d) and local.time() >= regular_close_time(d):
        return d
    return previous_trading_day(d)
