"""Ticker → company (CIK) on a date, from Polygon's reference records, and the weekly analysis times."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable, Mapping, Sequence

from marketlens.domain.market_calendar import NY, is_trading_day

ANALYSIS_TIME = time(20, 0)  # t: the last trading day of each week, 20:00 New York (docs/backtest/PREREGISTRATION.md)


@dataclass(frozen=True, slots=True)
class TickerRecord:
    ticker: str
    active: bool
    cik: int | None
    type: str | None
    name: str | None
    exchange: str | None
    delisted: date | None  # Polygon delisted_utc (date); None for the active record


@dataclass(frozen=True, slots=True)
class TickerInterval:
    ticker: str
    valid_from: date  # inclusive
    valid_to: date | None  # exclusive; None = still valid
    cik: int | None
    type: str | None
    name: str | None
    exchange: str | None


def build_ticker_map(records: Iterable[TickerRecord], sec_current: Mapping[str, int] | None = None) -> list[TickerInterval]:
    """One interval per record: a delisted record owns the ticker up to its delisting day (exclusive) since the
    previous record of that ticker ended; the active record owns it from the last delisting on. A reused ticker
    therefore maps to the earlier company before the reuse and to the new one after it; a renamed company keeps its
    CIK under both tickers. An active record without a CIK takes the SEC's current mapping of that ticker."""
    by_ticker: dict[str, list[TickerRecord]] = {}
    for r in records:
        by_ticker.setdefault(r.ticker, []).append(r)
    out: list[TickerInterval] = []
    for t, recs in by_ticker.items():
        gone = sorted((r for r in recs if not r.active and r.delisted is not None), key=lambda r: r.delisted)  # type: ignore[arg-type,return-value]
        start = date(1900, 1, 1)
        for r in gone:
            if r.delisted is not None and r.delisted > start:
                out.append(TickerInterval(t, start, r.delisted, r.cik, r.type, r.name, r.exchange))
                start = r.delisted
        for r in (x for x in recs if x.active):
            cik = r.cik if r.cik else (sec_current or {}).get(t)
            out.append(TickerInterval(t, start, None, cik, r.type, r.name, r.exchange))
            break
    return out


def lookup(intervals: Sequence[TickerInterval], day: date) -> TickerInterval | None:
    for iv in intervals:
        if iv.valid_from <= day and (iv.valid_to is None or day < iv.valid_to):
            return iv
    return None


def weekly_times(start: date, end: date) -> list[datetime]:
    """The analysis times t: the last trading day of every ISO week in [start, end], 20:00 New York."""
    days: dict[tuple[int, int], date] = {}
    d = start
    while d <= end:
        if is_trading_day(d):
            days[d.isocalendar()[:2]] = d  # later days of the same week replace earlier ones
        d += timedelta(days=1)
    return [datetime.combine(v, ANALYSIS_TIME, tzinfo=NY) for _, v in sorted(days.items())]
