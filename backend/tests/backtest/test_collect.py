"""Backtest stage 0: the collector end to end against fake providers (no network): the entitlement boundary, the
ticker → CIK map, unadjusted bars, pruning, splits, dividends, SEC quarters and the coverage counts."""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx

from marketlens.backtest import collect as C
from marketlens.backtest.schema import bt_engine, get_meta

TODAY = date(2026, 9, 28)
CUTOFF = date(2024, 10, 1)  # the fake plan reads sessions from here on
PEOPLE = {"AAA": 1045810, "BBB": 19617}  # CIKs the SEC fixture knows (NVDA, JPM)


def _poly_handler(req: httpx.Request) -> httpx.Response:
    p = req.url.path
    q = dict(req.url.params)
    assert "apiKey" in q
    if p.startswith("/v2/aggs/grouped/"):
        assert q.get("adjusted") == "false"  # as traded
        d = date.fromisoformat(p.rsplit("/", 1)[-1])
        if d < CUTOFF:
            return httpx.Response(403, json={"status": "NOT_AUTHORIZED"})
        rows = [{"T": t, "o": 100, "h": 101, "l": 99, "c": 100 + i, "v": 1e6} for i, t in enumerate(("AAA", "BBB"))]
        rows.append({"T": "TINY", "o": 1, "h": 1, "l": 1, "c": 1, "v": 100})  # never tradable: pruned
        rows.append({"T": "SPY", "o": 500, "h": 501, "l": 499, "c": 500, "v": 1e7})
        return httpx.Response(200, json={"status": "OK", "results": rows})
    if p == "/v3/reference/tickers":
        if q["active"] == "true":
            res = [{"ticker": "AAA", "cik": str(PEOPLE["AAA"]), "type": "CS"}, {"ticker": "BBB", "cik": str(PEOPLE["BBB"]), "type": "CS"},
                   {"ticker": "SPY", "type": "ETF"}, {"ticker": "TINY", "cik": "5", "type": "CS"}]
        else:
            res = [{"ticker": "AAA", "cik": "999", "type": "CS", "delisted_utc": "2024-01-02T00:00:00Z"}]  # an earlier holder of AAA
        return httpx.Response(200, json={"status": "OK", "results": res})
    if p == "/v3/reference/splits":
        return httpx.Response(200, json={"status": "OK", "results": [{"ticker": "AAA", "execution_date": "2025-06-10", "split_from": 1, "split_to": 4}]})
    if p == "/v3/reference/dividends":
        return httpx.Response(200, json={"status": "OK", "results": [{"ticker": "BBB", "ex_dividend_date": "2025-07-03", "cash_amount": 1.25, "currency": "USD", "dividend_type": "CD"}]})
    return httpx.Response(404, json={})


def test_the_collector_builds_a_point_in_time_database(tmp_path, monkeypatch):
    from marketlens.application.market_store import MarketStore
    from marketlens.infrastructure.db.session import make_session_factory
    from marketlens.providers.live.sec_edgar import SecEdgarProvider
    from tests.live_fixtures import live_transport

    monkeypatch.setattr(C, "last_completed_session", lambda _now: date(2025, 1, 31))  # a short window keeps the test fast
    db = str(tmp_path / "bt.db")
    eng = bt_engine(db)
    store = MarketStore(make_session_factory(eng), "LIVE")
    poly = C.Polygon("k", sleep=lambda _s: None, transport=httpx.MockTransport(_poly_handler))
    start = C.first_entitled_day(poly, TODAY)
    assert start == CUTOFF and poly.calls < 12  # binary search, not a walk
    C.collect_tickers(eng, poly)
    C.build_map(eng, {})
    C.collect_bars(eng, store, poly, TODAY, deadline=1e18)
    assert min(store.grouped_days()) == CUTOFF
    assert C.prune_bars(eng)["tickers_dropped"] == 1  # TINY
    C.collect_splits(eng, store, poly, start - timedelta(days=1460))
    C.collect_dividends(eng, poly, start)
    sec = SecEdgarProvider("MarketLens test test@example.com", transport=live_transport())
    C.collect_sec(eng, store, sec, deadline=1e18)
    assert json.loads(get_meta(eng, "done.sec"))["candidates"] == 2
    assert store.quarters("CIK0001045810", None)  # AAA's filings under its company key
    cov = C.coverage(eng, store)
    assert cov["first_session"] == CUTOFF.isoformat() and cov["cik_match_rate_stocks"] == 1.0
    assert cov["companies_with_sec_quarters"] >= 1 and cov["dividend_records"] == 1
