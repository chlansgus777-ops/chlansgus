"""A small backtest database for the engine and leak tests (fixture data — never used for measurement).

Securities (Polygon-shaped reference records):
- AAA: CIK 1045810 (the SEC fixture's NVDA filings), 4-for-1 split on SPLIT_DAY;
- BBB: CIK 19617 (the SEC fixture's JPM filings), a cash dividend on DIV_DAY;
- OLDC → NEWC: one company (CIK 1046179) renamed on RENAME_DAY (the old ticker's record is delisted then, the new
  ticker trades from that session on);
- GONE: CIK 999001, stops trading after GONE_LAST (a delisting);
- SPY: the benchmark ETF (no CIK).
Prices are deterministic smooth paths (as traded, unadjusted); FRED answers come from the live fixture, recorded
through the collector's RecordingTransport exactly as a real collection records them.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from sqlalchemy import insert

from marketlens.backtest import collect as C
from marketlens.backtest.schema import bt_dividends, bt_engine, bt_profiles, bt_tickers
from marketlens.domain.corporate_actions import SplitEvent
from marketlens.domain.fundamentals import QuarterlyFinancials
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import is_trading_day

START = date(2025, 1, 2)
END = date(2026, 6, 30)
SPLIT_DAY = date(2025, 11, 12)  # a Wednesday
DIV_DAY = date(2025, 11, 20)
RENAME_DAY = date(2025, 10, 1)  # OLDC's last session is the day before
GONE_LAST = date(2025, 12, 10)

BASE = {"AAA": 600.0, "BBB": 290.0, "OLDC": 40.0, "GONE": 60.0, "SPY": 600.0}
VOL = {"AAA": 3e6, "BBB": 1e7, "OLDC": 2e7, "GONE": 1e7, "SPY": 8e7}


def sessions(a: date = START, b: date = END) -> list[date]:
    out, d = [], a
    while d <= b:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def price(t: str, i: int) -> float:
    base = BASE["OLDC" if t == "NEWC" else t]
    return base * (1 + 0.0007 * i + 0.03 * math.sin(i / 9 + len(t)) + 0.01 * math.sin(i / 2.3 + 3 * len(t)))


def bars_for(day: date, i: int) -> dict[str, Bar]:
    out = {}
    for t in ("AAA", "BBB", "OLDC", "GONE", "SPY"):
        label = t
        if t == "OLDC" and day >= RENAME_DAY:
            label = "NEWC"
        if t == "GONE" and day > GONE_LAST:
            continue
        c = price(t, i)
        v = VOL[t]
        if t == "AAA" and day >= SPLIT_DAY:  # as traded: a quarter of the price, four times the shares
            c, v = c / 4, v * 4
        out[label] = Bar(day, round(c * 0.997, 4), round(c * 1.015, 4), round(c * 0.985, 4), round(c, 4), v)
    return out


# CIK → (base quarterly revenue, quarterly growth, gross margin, operating margin, shares outstanding)
COMPANY = {1045810: (9e9, 0.05, 0.70, 0.40, 6.1e9), 19617: (12e9, 0.01, 0.35, 0.15, 1.1e9),
           1046179: (5e9, 0.02, 0.30, 0.08, 9e8), 999001: (2e9, -0.03, 0.60, 0.05, 3e8)}
RESTATED = (date(2025, 6, 30), date(2025, 12, 15), 1.10)  # AAA: that quarter's revenue restated (+10 %) in a later filing


def quarters(rev0: float, g: float, gm: float, om: float, shares: float) -> list[QuarterlyFinancials]:
    """14 quarters ending 2022-12-31 … 2026-03-31, 10-Q filed 35 days after the quarter, 10-K 50 days."""
    out = []
    ends = []
    y, q = 2022, 4
    for _ in range(14):
        ends.append(date(y, 3 * q, 30 if q in (2, 3) else 31))
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    for i, end in enumerate(ends):
        form = "10-K" if end.month == 12 else "10-Q"
        filed = end + timedelta(days=50 if form == "10-K" else 35)
        rev = rev0 * (1 + g) ** i
        ni = rev * om * 0.8
        revisions = {}
        if shares == 6.1e9 and end == RESTATED[0]:
            revisions = {"revenue": ((RESTATED[1], rev * RESTATED[2]),)}
        out.append(QuarterlyFinancials(end, filed, f"{end.year}Q{(end.month - 1) // 3 + 1}", "sec", revenue=rev, gross_profit=rev * gm,
                                       operating_income=rev * om, net_income=ni, eps_diluted=ni / shares, operating_cash_flow=ni * 1.1,
                                       capex=rev * 0.05, sbc=rev * 0.02, depreciation_amortization=rev * 0.04, cash=rev * 0.8, total_debt=rev * 0.5,
                                       total_equity=rev * 3, shares_diluted=shares * 1.01, inventory=rev * 0.1, shares_outstanding=shares,
                                       revisions=revisions))
    return out


def build(path: str, fred: bool = True) -> str:
    from marketlens.application.market_store import MarketStore
    from marketlens.infrastructure.db.session import make_session_factory
    from tests.live_fixtures import live_transport

    eng = bt_engine(path)
    store = MarketStore(make_session_factory(eng), "LIVE")
    with eng.begin() as c:
        c.execute(insert(bt_tickers), [
            {"ticker": "AAA", "seq": 0, "active": True, "cik": 1045810, "type": "CS", "name": "AAA CORP", "exchange": "XNAS", "delisted": None},
            {"ticker": "BBB", "seq": 0, "active": True, "cik": 19617, "type": "CS", "name": "BBB BANK", "exchange": "XNYS", "delisted": None},
            {"ticker": "OLDC", "seq": 0, "active": False, "cik": 1046179, "type": "CS", "name": "OLD CO", "exchange": "XNYS", "delisted": RENAME_DAY},
            {"ticker": "NEWC", "seq": 0, "active": True, "cik": 1046179, "type": "CS", "name": "NEW CO", "exchange": "XNYS", "delisted": None},
            {"ticker": "GONE", "seq": 0, "active": False, "cik": 999001, "type": "CS", "name": "GONE INC", "exchange": "XNAS", "delisted": GONE_LAST + timedelta(days=1)},
            {"ticker": "SPY", "seq": 0, "active": True, "cik": None, "type": "ETF", "name": "SPDR S&P 500", "exchange": "ARCX", "delisted": None},
        ])
        c.execute(insert(bt_dividends), [{"ticker": "BBB", "ex_date": DIV_DAY, "seq": 0, "cash_amount": 1.4, "currency": "USD", "dividend_type": "CD",
                                          "declaration_date": DIV_DAY - timedelta(days=20), "pay_date": DIV_DAY + timedelta(days=10)}])
        c.execute(insert(bt_profiles), [
            {"cik": 1045810, "sector": "Technology", "industry": "Semiconductors", "sic": 3674, "payload": "{}"},
            {"cik": 19617, "sector": "Industrials", "industry": "Machinery", "sic": 3560, "payload": "{}"},
            {"cik": 1046179, "sector": "Consumer Discretionary", "industry": "Retail", "sic": 5331, "payload": "{}"},
            {"cik": 999001, "sector": "Technology", "industry": "Software", "sic": 7372, "payload": "{}"},
        ])
    C.build_map(eng, {})
    for i, d in enumerate(sessions()):
        store.save_grouped(d, bars_for(d, i), "polygon")
    store.save_splits([SplitEvent("AAA", SPLIT_DAY, 1, 4, "polygon")])
    for cik, spec in COMPANY.items():
        store.save_quarters(f"CIK{cik:010d}", quarters(*spec))
    if fred:
        C.collect_fred(eng, "fixture-key", date(2025, 10, 1), END, deadline=1e18, inner=live_transport())
    return path

