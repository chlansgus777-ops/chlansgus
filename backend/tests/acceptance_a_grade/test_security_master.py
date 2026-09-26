"""Evaluation 2 item 13: a ticker is a label, the SEC CIK is the company. Renames keep history; a reused
ticker never inherits another company's prices or financials."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

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
    assert out["reused"] == 0 and out["relisted"] == 1
    # since evaluation 4 (M2) a relist is a new listing interval: the delisting stays in history, the old bars stay
    # with the old interval (reached through resolve() for a recommendation made then), no price bridge over the gap
    old = st.resolve("XYZ", D1)
    assert old != "XYZ" and len(st.bars(old, D1 - timedelta(days=20), D2)) == 10
    assert {s.ticker for s in st.securities(D1 + timedelta(days=3))} == set()


def test_a_few_days_missing_from_the_listing_is_a_data_gap_not_a_relist(tmp_path):
    st = _store(tmp_path)
    st.sync_universe([_sec("XYZ", 9)], D1)
    _bars(st, "XYZ", D1 - timedelta(days=10), 10, 20.0)
    st.sync_universe([], D1 + timedelta(days=1))
    out = st.sync_universe([_sec("XYZ", 9)], D1 + timedelta(days=3))
    assert out["relisted"] == 0 and len(st.bars("XYZ", D1 - timedelta(days=20), D2)) == 10


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


def _session_days(a, b):
    from marketlens.domain.market_calendar import is_trading_day

    out, d = [], a
    while d <= b:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def test_bars_that_only_resume_after_a_long_absence_are_a_relist_not_a_gap(tmp_path):
    """Evidence rule (evaluation 5, L2): continuous trading through the absence = data gap; a hole in the bars
    for the whole absence (trading resumed only at the relisting) = a real delisting and a new interval."""
    st = _store(tmp_path)
    st.sync_universe([_sec("RRR", 4)], D1)
    st.save_bars("RRR", [Bar(d, 10, 10, 10, 10, 1e6) for d in _session_days(D1 - timedelta(days=60), D1)], "polygon")
    st.sync_universe([], D1 + timedelta(days=1))
    back = D1 + timedelta(days=60)
    st.save_bars("RRR", [Bar(d, 12, 12, 12, 12, 1e6) for d in _session_days(back - timedelta(days=2), back)], "polygon")
    out = st.sync_universe([_sec("RRR", 4)], back)
    assert out["relisted"] == 1
    with st.sf() as s:
        assert repo.delisted_on(s, st.resolve("RRR", D1), "LIVE") == D1 + timedelta(days=1)


def test_continuous_trading_through_a_long_absence_is_a_data_gap(tmp_path):
    st = _store(tmp_path)
    st.sync_universe([_sec("TTT", 3)], D1)
    st.save_bars("TTT", [Bar(d, 10, 10, 10, 10, 1e6) for d in _session_days(D1 - timedelta(days=60), D1 + timedelta(days=30))], "polygon")
    st.sync_universe([], D1 + timedelta(days=1))
    out = st.sync_universe([_sec("TTT", 3)], D1 + timedelta(days=30))
    assert out["relisted"] == 0 and st.resolve("TTT", D1) == "TTT"
    with st.sf() as s:
        assert repo.delisted_on(s, "TTT", "LIVE") is None


def test_an_undecidable_class_rename_is_unresolved_never_a_final_delisting(tmp_path):
    """Two classes vanish and two new tickers appear whose class suffix cannot be matched: nothing is linked
    (no wrong history), and the vanished names are UNRESOLVED — not delisted — so outcomes stay pending."""
    st = _store(tmp_path)
    st.sync_universe([_sec("XA", 9), _sec("XB", 9)], D1)
    _bars(st, "XA", D1 - timedelta(days=5), 5, 600.0)
    _bars(st, "XB", D1 - timedelta(days=5), 5, 400.0)
    out = st.sync_universe([_sec("QQ1", 9), _sec("QQ2", 9)], D2)
    assert out["unresolved"] == 2 and out["delisted"] == 0 and out["renamed"] == 0
    with st.sf() as s:
        assert repo.delisted_on(s, "XA", "LIVE") is None and repo.delisted_on(s, "XB", "LIVE") is None
    assert st.bars("QQ1", D1 - timedelta(days=10), D2) == [] and st.bars("QQ2", D1 - timedelta(days=10), D2) == []
    assert {s.ticker for s in st.securities(D2)} == {"QQ1", "QQ2"}  # one row per company-class and day
    assert {s.ticker for s in st.securities(D1)} == {"XA", "XB"}


def test_a_long_retired_class_does_not_block_the_rename_of_the_listed_one(tmp_path):
    st = _store(tmp_path)
    st.sync_universe([_sec("OLDA", 5), _sec("XYZA", 5)], D1)
    st.sync_universe([_sec("XYZA", 5)], D1 + timedelta(days=1))
    _bars(st, "XYZA", D1 - timedelta(days=5), 5, 30.0)
    out = st.sync_universe([_sec("NEWA", 5)], D2 + timedelta(days=30))
    assert out["renamed"] == 1 and len(st.bars("NEWA", D1 - timedelta(days=10), D2 + timedelta(days=30))) == 5


def test_audit_history_reports_records_an_earlier_version_may_have_written_wrong(tmp_path):
    """Evaluation 5, R7: rows written before these fixes are not rewritten automatically; the audit finds them."""
    from marketlens.infrastructure.db.models import SecurityRow

    st = _store(tmp_path)
    st.sync_universe([_sec("GAP", 1), _sec("OLDN", 2), _sec("NOCIK", None)], D1)
    st.save_bars("GAP", [Bar(D1 + timedelta(days=i), 1, 1, 1, 1, 1) for i in range(1, 5)], "polygon")
    with st.sf() as s:  # simulate what an earlier version stored: plain delistings, an unlinked new ticker
        for t in ("GAP", "OLDN"):
            r = s.get(SecurityRow, t)
            r.active, r.delisted_at = False, D1
        s.add(SecurityRow(ticker="NEWN", company_name="n", exchange="NASDAQ", sector="Unknown", industry="Unknown", market_cap=None, active=True,
                          mode="LIVE", updated_at=datetime(2026, 6, 3, tzinfo=timezone.utc), first_seen=D1 + timedelta(days=2), cik=2))
        s.commit()
    kinds = {(f["kind"], f["ticker"]) for f in st.audit_history()}
    assert ("TRADED_AFTER_DELISTING", "GAP") in kinds
    assert ("DELISTING_LOOKS_LIKE_RENAME", "OLDN") in kinds
    assert ("NO_CIK", "NOCIK") in kinds
    with st.sf() as s:  # read-only
        assert s.get(SecurityRow, "GAP").delisted_at == D1
