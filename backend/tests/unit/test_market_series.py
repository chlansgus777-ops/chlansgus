"""Owner 2026-10-04 "나스닥100, 러셀2000 등 없음되있는건 왜 그런거야": FRED does not publish the Russell 2000 or the PHLX
semiconductor index, and market breadth is not a FRED series. LIVE now reads them from the app's own daily bars: the
tracking ETF for the index (its level labelled as the ETF's price) and the share of common stocks above their 200-day
average — only sessions closed at ``as_of`` (no look-ahead), and never replacing a series FRED did give."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from marketlens.application.data_access import DataAccess
from marketlens.domain.facts import Fact
from marketlens.domain.macro import BREADTH_ABOVE_200D, NDX, RUT, SOX, MacroSeries
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import is_trading_day


def _days(end: date, n: int) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


class Store:
    def __init__(self, end: date) -> None:
        days = _days(end + timedelta(days=3), 260)  # bars AFTER ``end`` exist too: they must not be read
        self.series: dict[str, list[Bar]] = {}
        for i in range(250):  # 150 rising (above their 200-day average), 100 falling
            up = i < 150
            self.series[f"S{i:03d}"] = [Bar(d, 0, 0, 0, 100 + (k if up else -k) * 0.1, 1e6) for k, d in enumerate(days)]
        self.series["IWM"] = [Bar(d, 0, 0, 0, 200 + k * 0.1, 1e6) for k, d in enumerate(days)]
        self.series["SPY"] = [Bar(d, 0, 0, 0, 500, 1e6) for d in days]

    def bars(self, t: str, start: date, end: date) -> list[Bar]:
        return [b for b in self.series.get(t, []) if start <= b.day <= end]

    def last_bars_all(self, start: date, end: date) -> dict[str, list[Bar]]:
        return {t: [b for b in bs if start <= b.day <= end] for t, bs in self.series.items()}

    def securities(self, _on):  # noqa: ANN001, ANN202
        from types import SimpleNamespace
        return [SimpleNamespace(ticker="IWM", is_etf=True), SimpleNamespace(ticker="SPY", is_etf=True)]


def _da(store: Store) -> DataAccess:
    da = DataAccess.__new__(DataAccess)
    da.store = store
    da._breadth_cache = None
    return da


def test_the_indices_fred_lacks_come_from_their_etfs_and_breadth_from_the_bars():
    as_of = datetime(2026, 10, 2, 22, 0, tzinfo=timezone.utc)  # Friday after the close
    end = date(2026, 10, 2)
    store = Store(end)
    fred_ndx = MacroSeries(NDX, Fact(25000.0, "fred:NASDAQ100"))
    got = _da(store)._market_series(as_of, {NDX: fred_ndx})
    assert NDX not in got  # FRED gave it: kept
    r = got[RUT]
    iwm = [b.close for b in store.series["IWM"] if b.day <= end]
    assert r.latest.source == "etf:IWM" and r.latest.value == pytest.approx(iwm[-1]) and "ETF" in r.latest.note
    assert r.pct_change_20d == pytest.approx(iwm[-1] / iwm[-21] - 1) and r.above_200d is True
    assert SOX not in got  # no SOXX bars stored: missing, never invented
    b = got[BREADTH_ABOVE_200D]
    assert b.latest.value == pytest.approx(0.6) and b.latest.source == "calc:breadth"  # 150 of 250 stocks; ETFs excluded


def test_a_session_still_open_is_not_read():
    as_of = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)  # Friday 11:00 ET: the last CLOSED session is Thursday
    store = Store(date(2026, 10, 2))
    r = _da(store)._market_series(as_of, {})[RUT]
    assert r.latest.source_ts.date() == date(2026, 10, 1)
