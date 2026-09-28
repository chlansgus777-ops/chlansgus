"""Alpaca 2016+ collector (marketlens/backtest/alpaca.py) against fake Alpaca / Polygon answers — no network.

Cases: a reused ticker (two companies, one boundary in the map), a rename (FB → META, literal symbols), a 60-day gap,
a halving with no split recorded, a recorded 4-for-1 split, a boundary the map puts five sessions late, a delisting
only Alpha Vantage knows, a symbol Alpaca rejects, a never-tradable ticker, bars after a ticker's last listing, and
the overlap with Polygon's first sessions."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import insert, select

from marketlens.backtest import alpaca as A
from marketlens.backtest.identity import TickerInterval
from marketlens.backtest.schema import bt_engine, bt_ticker_map, bt_unresolved, get_meta, set_meta
from marketlens.domain.market_calendar import is_trading_day

POLY_START = date(2020, 9, 1)
OPEN = date(1900, 1, 1)
MAP = [  # ticker, valid_from, valid_to, cik, type
    ("AAC", OPEN, date(2019, 6, 3), 111, "CS"), ("AAC", date(2019, 6, 3), None, 222, "CS"),
    ("FB", OPEN, date(2020, 6, 1), 333, "CS"), ("META", OPEN, None, 333, "CS"),
    ("TINY", OPEN, None, 444, "CS"), ("GAPPY", OPEN, None, 555, "CS"), ("SPLT", OPEN, None, 666, "CS"),
    ("NOSPL", OPEN, None, 777, "CS"), ("HIDN", OPEN, None, 888, "CS"), ("OLDX", OPEN, date(2017, 1, 3), 999, "CS"),
    ("MIS", OPEN, date(2018, 1, 10), 1010, "CS"), ("MIS", date(2018, 1, 10), None, 1011, "CS"),
    ("ODD$", OPEN, None, 1212, "CS"), ("SPY", OPEN, None, None, "ETF"), ("WRNT", OPEN, None, None, "WARRANT"),
]


def _days(a: date, b: date) -> list[date]:
    out, d = [], a
    while d <= b:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _price(sym: str, d: date) -> tuple[float, float] | None:
    """(close, volume) the fake SIP feed has for a literal symbol on a day, or None (no trade)."""
    if sym == "AAC":
        return (10.0, 1e6) if d < date(2019, 6, 3) else (50.0, 1e6)
    if sym == "FB":
        return (200.0, 1e6) if d < date(2020, 6, 1) else None
    if sym == "META":
        return (230.0, 1e6) if d >= date(2020, 6, 1) else None
    if sym == "TINY":
        return (1.0, 100.0)
    if sym == "GAPPY":
        return None if date(2017, 3, 1) <= d < date(2017, 5, 15) else (30.0, 1e6)
    if sym == "SPLT":
        return (400.0, 1e6) if d < date(2018, 5, 1) else (100.0, 4e6)
    if sym == "NOSPL":
        return (100.0, 1e6) if d < date(2018, 7, 2) else (50.0, 1e6)
    if sym == "HIDN":
        return (20.0, 1e6)
    if sym == "OLDX":
        return (15.0, 1e6)  # keeps printing after its listing ended (another holder the map does not know)
    if sym == "MIS":
        return (40.0, 1e6) if d < date(2018, 1, 5) else (5.0, 1e7)  # the real switch is five sessions before the map's
    if sym == "SPY":
        return (300.0, 1e8)
    return None


class FakeAlpaca:
    def __init__(self, page: int = 700) -> None:
        self.page = page
        self.requests: list[dict[str, str]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        q = dict(req.url.params)
        self.requests.append(q)
        assert req.headers["APCA-API-KEY-ID"] == "kid" and req.headers["APCA-API-SECRET-KEY"] == "sec"
        assert (q["asof"], q["adjustment"], q["feed"], q["timeframe"]) == ("-", "raw", "sip", "1Day")  # literal symbols, as traded
        syms = q["symbols"].split(",")
        if "ODD$" in syms:
            return httpx.Response(400, json={"message": "invalid symbol: ODD$"})
        start, end = date.fromisoformat(q["start"]), date.fromisoformat(q["end"])
        flat = [(s, d, p) for s in sorted(syms) for d in _days(start, end) if (p := _price(s, d)) is not None]
        off = int(q.get("page_token") or 0)
        chunk = flat[off:off + self.page]
        bars: dict[str, list[dict]] = {}
        for s, d, (c, v) in chunk:
            bars.setdefault(s, []).append({"t": f"{d.isoformat()}T04:00:00Z", "o": c, "h": c, "l": c, "c": c, "v": v})
        nxt = str(off + self.page) if off + self.page < len(flat) else None
        return httpx.Response(200, json={"bars": bars, "next_page_token": nxt})


def _db(tmp_path):
    from marketlens.application.market_store import MarketStore
    from marketlens.domain.corporate_actions import SplitEvent
    from marketlens.infrastructure.db.models import PriceBarRow as P
    from marketlens.infrastructure.db.session import make_session_factory

    eng = bt_engine(str(tmp_path / "bt.db"))
    store = MarketStore(make_session_factory(eng), "LIVE")
    with eng.begin() as c:
        c.execute(insert(bt_ticker_map), [{"ticker": t, "valid_from": a, "valid_to": b, "cik": k, "type": ty, "name": t, "exchange": "XNYS"} for t, a, b, k, ty in MAP])
        now = datetime.now(timezone.utc)
        rows = []
        for d in _days(POLY_START, POLY_START + timedelta(days=20)):  # the Polygon window's first sessions
            rows.append({"ticker": "SPY", "day": d, "source": "polygon", "open": 300, "high": 300, "low": 300, "close": 300.0, "volume": 1e8, "retrieved_at": now})
            rows.append({"ticker": "META", "day": d, "source": "polygon", "open": 230, "high": 230, "low": 230, "close": 231.0, "volume": 1e6, "retrieved_at": now})  # 0.4 %: agrees
            rows.append({"ticker": "AAC", "day": d, "source": "polygon", "open": 60, "high": 60, "low": 60, "close": 60.0, "volume": 1e6, "retrieved_at": now})  # disagrees
        c.execute(insert(P.__table__), rows)
    store.save_splits([SplitEvent("SPLT", date(2018, 5, 1), 1.0, 4.0, "polygon")])
    set_meta(eng, "done.bars", {"start": POLY_START.isoformat()})
    set_meta(eng, "bars.start", POLY_START.isoformat())
    return eng, store


AV_ROWS = [
    {"symbol": "HIDN", "name": "Hidden Holder Inc", "assetType": "Stock", "delistingDate": "2018-03-01"},  # the map has no end for it
    {"symbol": "AAC", "name": "Old AAC", "assetType": "Stock", "delistingDate": "2019-05-30"},  # matches the map's boundary (4 days)
    {"symbol": "OLDX", "name": "Old X", "assetType": "Stock", "delistingDate": "2016-12-30"},
] + [{"symbol": f"B{i}", "assetType": "Stock", "delistingDate": "2019-01-02"} for i in range(60)]  # a bulk date: ignored


def test_collects_2016_bars_by_listing_interval_and_marks_what_it_cannot_resolve(tmp_path, monkeypatch):
    eng, store = _db(tmp_path)
    fake = FakeAlpaca()
    alp = A.Alpaca("kid", "sec", sleep=lambda _s: None, transport=httpx.MockTransport(fake))
    st = A.collect_alpaca(eng, store, alp, AV_ROWS, date(2016, 1, 1), POLY_START, deadline=1e18)

    assert st["complete"] and json.loads(get_meta(eng, "done.alpaca"))["start"] == "2016-01-01"
    assert "WRNT" not in {s for q in fake.requests for s in q["symbols"].split(",")}  # only stock listings and the benchmark
    assert set(st["rejected"]) == {"ODD$"}  # taken out of its batch and reported
    assert st["pruned_tickers"] == 1  # TINY never reached $2M a day
    assert st["rows_outside_intervals"] > 0  # OLDX after its listing ended
    assert any("page_token" in q for q in fake.requests)  # paged
    with eng.connect() as c:
        un = {(r.ticker, r.reason) for r in c.execute(select(bt_unresolved))}
        from marketlens.infrastructure.db.models import PriceBarRow as P
        stored = {t for (t,) in c.execute(select(P.ticker).where(P.source == "alpaca").distinct())}
        last_day = c.execute(select(P.day).where(P.source == "alpaca").order_by(P.day.desc()).limit(1)).scalar()
    assert ("GAPPY", "gap") in un
    assert ("NOSPL", "unrecorded_split") in un
    assert ("HIDN", "av_delisting_not_in_map") in un
    assert ("MIS", "boundary_jump") in un  # the old interval's last sessions hold the new company's prices
    flagged = {t for t, _ in un}
    assert not flagged & {"AAC", "FB", "META", "SPLT", "SPY", "OLDX"}  # a correct boundary, a rename, a recorded split
    assert "TINY" not in stored and {"AAC", "FB", "META", "SPY", "GAPPY"} <= stored
    assert last_day < POLY_START  # the Polygon window is never written twice
    # overlap with Polygon's first sessions: SPY and META agree, AAC does not
    assert st["overlap_pairs"] > 0 and st["overlap_match"] < st["overlap_pairs"] and st["overlap_mismatch_tickers"] == ["AAC"]
    assert min(store.grouped_days()) < date(2016, 1, 10)  # the earlier sessions count as loaded


def test_the_backtest_leaves_unresolved_intervals_out_and_keeps_a_reused_tickers_two_companies_apart(tmp_path):
    from marketlens.backtest.store import BacktestData
    from marketlens.infrastructure.db.session import make_session_factory

    eng, store = _db(tmp_path)
    alp = A.Alpaca("kid", "sec", sleep=lambda _s: None, transport=httpx.MockTransport(FakeAlpaca()))
    A.collect_alpaca(eng, store, alp, AV_ROWS, date(2016, 1, 1), POLY_START, deadline=1e18)
    data = BacktestData(eng, make_session_factory(eng))
    keys = {ln.key.split("@")[0] for ln in data.lineages}
    assert "GAPPY" not in keys and "NOSPL" not in keys and "HIDN" not in keys
    assert data.unresolved_rows_skipped > 0
    aac = [ln for ln in data.lineages if "AAC" in ln.labels]
    assert {ln.cik for ln in aac} == {111, 222}  # two companies, never one spliced series
    old = next(ln for ln in aac if ln.cik == 111)
    assert old.days[-1] < date(2019, 6, 3) and {r[3] for r in old.rows} == {10.0}
    meta = [ln for ln in data.lineages if ln.cik == 333]
    assert len(meta) == 1 and set(meta[0].labels) == {"FB", "META"}  # FB → META joined as one company, no double count
    assert data.first_session is not None and data.first_session.year == 2016


def test_a_deadline_stops_between_batches_and_the_next_run_resumes(tmp_path):
    eng, store = _db(tmp_path)
    fake = FakeAlpaca()
    alp = A.Alpaca("kid", "sec", sleep=lambda _s: None, transport=httpx.MockTransport(fake))
    st = A.collect_alpaca(eng, store, alp, AV_ROWS, date(2016, 1, 1), POLY_START, deadline=0)  # already past
    assert not st["complete"] and get_meta(eng, "done.alpaca") is None
    st2 = A.collect_alpaca(eng, store, alp, AV_ROWS, date(2016, 1, 1), POLY_START, deadline=1e18)
    assert st2["complete"]


def test_rejected_symbols_are_found_by_splitting_the_batch_when_alpaca_does_not_name_them():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        syms = req.url.params["symbols"].split(",")
        calls.append(syms)
        if "BAD" in syms:
            return httpx.Response(400, json={"message": "invalid request"})
        return httpx.Response(200, json={"bars": {s: [{"t": "2016-01-04T05:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}] for s in syms}, "next_page_token": None})

    alp = A.Alpaca("kid", "sec", sleep=lambda _s: None, transport=httpx.MockTransport(handler))
    got, bad = alp.bars(["AAA", "BAD", "CCC", "DDD"], date(2016, 1, 1), date(2016, 1, 10))
    assert set(bad) == {"BAD"} and set(got) == {"AAA", "CCC", "DDD"}


def test_rate_limit_answers_wait_and_are_never_retried_faster():
    waits: list[float] = []
    n = {"i": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(429, json={"message": "too many requests"})
        return httpx.Response(200, json={"bars": {}, "next_page_token": None})

    alp = A.Alpaca("kid", "sec", sleep=waits.append, transport=httpx.MockTransport(handler))
    alp.bars(["AAA"], date(2016, 1, 1), date(2016, 1, 10))
    assert any(w >= 60 for w in waits)


def test_interval_checks():
    iv = TickerInterval("X", OPEN, None, 1, "CS", "X", "XNYS")
    days = _days(date(2018, 1, 2), date(2018, 3, 30))
    rows = [A.Row(d, 10, 10, 10, 10.0 if i < 30 else 3.0, 1e6) for i, d in enumerate(days)]  # a −70 % day mid-interval
    assert A.check_interval(rows, iv, [], (date(2016, 1, 1), POLY_START)) == []  # a real crash is kept (no survivorship filter)
    split = [(days[30], 1.0, 3.0)]
    rows3 = [A.Row(d, 1, 1, 1, 30.0 if i < 30 else 10.1, 1e6) for i, d in enumerate(days)]
    assert A.check_interval(rows3, iv, split, (date(2016, 1, 1), POLY_START)) == []  # explained by the recorded 3-for-1
    assert A.check_interval(rows3, iv, [], (date(2016, 1, 1), POLY_START))[0][0] == "unrecorded_split"
    rows_bad_split = [A.Row(d, 1, 1, 1, 30.0, 1e6) for d in days]
    assert A.check_interval(rows_bad_split, iv, split, (date(2016, 1, 1), POLY_START))[0][0] == "split_mismatch"


def test_alpha_vantage_cross_check_ignores_bulk_dates_and_matched_boundaries():
    ivs = {"AAC": [TickerInterval("AAC", OPEN, date(2019, 6, 3), 1, "CS", "", ""), TickerInterval("AAC", date(2019, 6, 3), None, 2, "CS", "", "")],
           "HIDN": [TickerInterval("HIDN", OPEN, None, 3, "CS", "", "")], "B7": [TickerInterval("B7", OPEN, None, 4, "CS", "", "")]}
    out = A.av_cross_check(AV_ROWS, ivs, (date(2016, 1, 1), POLY_START))
    assert [(t, r) for t, _vf, r, _d in out] == [("HIDN", "av_delisting_not_in_map")]


def test_earlier_splits_and_dividends_are_added_without_touching_the_polygon_years(tmp_path):
    from marketlens.backtest import collect as C
    from marketlens.backtest.schema import bt_dividends

    eng, store = _db(tmp_path)
    set_meta(eng, "done.splits", {"events": 1, "since": "2020-09-01"})
    set_meta(eng, "done.dividends", {"records": 1, "since": "2020-09-01"})
    with eng.begin() as c:
        c.execute(insert(bt_dividends).values(ticker="SPY", ex_date=date(2021, 3, 19), seq=1, cash_amount=1.26, currency="USD"))
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        q = dict(req.url.params)
        seen.append({"path": req.url.path, **q})
        if req.url.path == "/v3/reference/splits":
            assert q["execution_date.lt"] == "2020-09-01" and q["execution_date.gte"] == "2015-01-01"
            return httpx.Response(200, json={"status": "OK", "results": [{"ticker": "AAPL", "execution_date": "2020-08-31", "split_from": 1, "split_to": 4}]})
        assert q["ex_dividend_date.lt"] == "2020-09-01"
        return httpx.Response(200, json={"status": "OK", "results": [{"ticker": "SPY", "ex_dividend_date": "2019-03-15", "cash_amount": 1.23, "currency": "USD"}]})

    poly = C.Polygon("k", sleep=lambda _s: None, transport=httpx.MockTransport(handler))
    A.collect_early_reference(eng, store, poly, date(2016, 1, 1), POLY_START)
    assert [s.execution_date for s in store.splits("AAPL")] == [date(2020, 8, 31)]
    with eng.connect() as c:
        assert sorted(r.ex_date for r in c.execute(select(bt_dividends))) == [date(2019, 3, 15), date(2021, 3, 19)]
    A.collect_early_reference(eng, store, poly, date(2016, 1, 1), POLY_START)
    assert len(seen) == 2  # done once: a resumed run does not ask again


def test_run_refuses_a_database_whose_polygon_years_are_not_finished(tmp_path):
    bt_engine(str(tmp_path / "empty.db"))
    with pytest.raises(SystemExit):
        A.run(str(tmp_path / "empty.db"))


def test_alpha_vantage_list_is_one_spaced_call_and_a_burst_answer_is_retried_once():
    sleeps: list[float] = []
    n = {"i": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        n["i"] += 1
        assert req.url.params["state"] == "delisted" and req.url.params["function"] == "LISTING_STATUS"
        if n["i"] == 1:
            return httpx.Response(200, text="{}")
        return httpx.Response(200, text="symbol,name,exchange,assetType,ipoDate,delistingDate,status\nHIDN,Hidden,NYSE,Stock,2010-01-04,2018-03-01,Delisted\n")

    rows = A.av_delisted("key", sleep=sleeps.append, transport=httpx.MockTransport(handler))
    assert rows[0]["symbol"] == "HIDN" and sleeps == [15, 65]


def test_bars_start_reads_back_whether_stored_plain_or_json_quoted(tmp_path):
    """Run 36396716134 collected every day to 2026-09-25, then crashed on json.loads("2016-01-04"): set_meta stores a
    str as is, so the date must read back from a plain ISO string (and from an older JSON-quoted one)."""
    from datetime import date as _d

    from marketlens.backtest.schema import bt_engine, get_meta_date, set_meta

    eng = bt_engine(str(tmp_path / "m.db"))
    set_meta(eng, "bars.start", "2016-01-04")
    assert get_meta_date(eng, "bars.start") == _d(2016, 1, 4)
    set_meta(eng, "bars.start", '"2016-01-05"')
    assert get_meta_date(eng, "bars.start") == _d(2016, 1, 5)
    assert get_meta_date(eng, "missing") is None
