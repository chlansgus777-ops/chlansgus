"""Evaluation 2 item 13: a ticker is a label, the SEC CIK is the company. Renames keep history; a reused
ticker never inherits another company's prices or financials."""

from __future__ import annotations

from datetime import date, timedelta

from marketlens.domain.enums import Exchange
from marketlens.domain.fundamentals import QuarterlyFinancials
from marketlens.domain.market import Bar, Security
from marketlens.infrastructure.db import repository as repo
from tests.acceptance_a_grade.test_research_integrity import _store

D1, D2 = date(2026, 6, 1), date(2026, 6, 10)


def _sec(t: str, cik: int | None, name: str = "Co") -> Security:
    return Security(t, name, Exchange.NASDAQ, "Unknown", "Unknown", None, cik=cik)


def _bars(st, t: str, start: date, n: int, px: float) -> None:
    st.save_bars(t, [Bar(start + timedelta(days=i), px, px + 1, px - 1, px, 1e6) for i in range(n)], "polygon")


def test_rename_keeps_one_company_with_continuous_history(tmp_path):
    st = _store(tmp_path)
    st.sync_universe([_sec("FB", 1326801, "Facebook"), _sec("OTHER", 5)], D1)
    _bars(st, "FB", D1 - timedelta(days=30), 30, 300.0)
    st.set_profile("FB", {"sic": 7370, "sector": "Technology", "industry": "Internet"})
    out = st.sync_universe([_sec("META", 1326801, "Meta Platforms"), _sec("OTHER", 5)], D2)
    assert out["renamed"] == 1 and out["delisted"] == 0  # a rename is not a delisting
    _bars(st, "META", D2, 3, 310.0)
    got = st.bars("META", D1 - timedelta(days=40), D2 + timedelta(days=5))
    assert len(got) == 33 and got[0].close == 300.0 and got[-1].close == 310.0
    assert len(st.last_bars_all(D1 - timedelta(days=40), D2 + timedelta(days=5))["META"]) == 33
    assert len(st.bars("FB", D1 - timedelta(days=40), D2 + timedelta(days=5))) == 33  # an old FB recommendation follows the company
    new = {s.ticker: s for s in st.securities(None)}
    assert "META" in new and "FB" not in new and new["META"].sector == "Technology"
    assert {s.ticker for s in st.securities(D1)} == {"FB", "OTHER"}  # one company per day in the historical universe
    assert {s.ticker for s in st.securities(D2)} == {"META", "OTHER"}
    with st.sf() as s:
        assert repo.delisted_on(s, "FB", "LIVE") is None


def test_reused_ticker_does_not_inherit_the_old_companys_data(tmp_path):
    st = _store(tmp_path)
    st.sync_universe([_sec("ABC", 111, "Old Co")], D1)
    _bars(st, "ABC", D1 - timedelta(days=30), 30, 50.0)
    st.save_quarters("ABC", [QuarterlyFinancials(date(2026, 3, 31), date(2026, 5, 1), "Q1", "sec-edgar", revenue=1e9)])
    st.sync_universe([], D1 + timedelta(days=1))  # Old Co delisted
    out = st.sync_universe([_sec("ABC", 222, "New Co")], D2)  # the ticker comes back for another company
    assert out["reused"] == 1
    assert st.bars("ABC", D1 - timedelta(days=60), D2) == []  # nothing of Old Co
    assert st.quarters("ABC", None) is None
    assert len(st.bars("ABC~111", D1 - timedelta(days=60), D2)) == 30 and st.quarters("ABC~111", None)
    assert st.resolve("ABC", D1 - timedelta(days=5)) == "ABC~111" and st.resolve("ABC", D2) == "ABC"
    hist = {s.ticker: s for s in st.securities(D1)}
    assert "ABC~111" in hist and "ABC" not in hist  # survivorship: the old company stays in the past universe
    now = {s.ticker: s for s in st.securities(None)}
    assert now["ABC"].company_name == "New Co" and now["ABC"].cik == 222 and now["ABC"].sector == "Unknown"


def test_relisting_of_the_same_company_is_not_a_reuse(tmp_path):
    st = _store(tmp_path)
    st.sync_universe([_sec("XYZ", 9)], D1)
    _bars(st, "XYZ", D1 - timedelta(days=10), 10, 20.0)
    st.sync_universe([], D1 + timedelta(days=1))
    out = st.sync_universe([_sec("XYZ", 9)], D2)
    assert out["reused"] == 0 and len(st.bars("XYZ", D1 - timedelta(days=20), D2)) == 10


def test_rows_stored_before_the_security_master_adopt_the_cik(tmp_path):
    st = _store(tmp_path)
    st.sync_universe([_sec("OLD", None)], D1)
    _bars(st, "OLD", D1 - timedelta(days=10), 10, 20.0)
    out = st.sync_universe([_sec("OLD", 77)], D2)
    assert out["reused"] == 0 and next(s for s in st.securities(None) if s.ticker == "OLD").cik == 77
    assert len(st.bars("OLD", D1 - timedelta(days=20), D2)) == 10


def test_share_classes_of_one_cik_are_not_a_rename(tmp_path):
    st = _store(tmp_path)
    st.sync_universe([_sec("GOOGL", 1652044)], D1)
    out = st.sync_universe([_sec("GOOGL", 1652044), _sec("GOOG", 1652044)], D2)
    assert out["renamed"] == 0 and out["added"] == 1
    assert {s.ticker for s in st.securities(None)} == {"GOOGL", "GOOG"}


def test_relist_keeps_the_delisting_and_does_not_bridge_the_price_gap(tmp_path):
    st = _store(tmp_path)
    st.sync_universe([_sec("OLDT", 5)], D1)
    _bars(st, "OLDT", D1 - timedelta(days=10), 10, 20.0)
    st.sync_universe([], D1 + timedelta(days=1))
    out = st.sync_universe([_sec("NEWT", 5)], D2 + timedelta(days=60))
    assert out["relisted"] == 1 and out["renamed"] == 0
    with st.sf() as s:
        assert repo.delisted_on(s, "OLDT", "LIVE") == D1 + timedelta(days=1)  # outcomes of old calls see the delisting
    assert st.bars("NEWT", D1 - timedelta(days=20), D2 + timedelta(days=90)) == []  # no history across the gap


def test_listing_order_does_not_change_a_same_day_ticker_swap(tmp_path):
    for order in (0, 1):
        st = _store(tmp_path / str(order))
        st.sync_universe([_sec("FB", 1), _sec("META", 2)], D1)
        _bars(st, "FB", D1 - timedelta(days=5), 5, 300.0)
        _bars(st, "META", D1 - timedelta(days=5), 5, 15.0)
        todays = [_sec("META", 1), _sec("METV", 2)]
        st.sync_universe(todays if order == 0 else todays[::-1], D2)
        assert [b.close for b in st.bars("META", D1 - timedelta(days=10), D2)] == [300.0] * 5
        assert [b.close for b in st.bars("METV", D1 - timedelta(days=10), D2)] == [15.0] * 5
