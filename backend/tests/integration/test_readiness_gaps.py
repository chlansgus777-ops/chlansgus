"""Owner report 2026-09-28 — an hour after "데이터 준비 시작": "시가총액 확인 종목 57 % < 80 %, 대형주 분기 재무 76 %
(재무 태그 해석 불가 80종목), 실패한 항목을 다시 시도할 수 있는 시각: 2026-10-04".

1. (measured) The shares stay with the company's primary ticker: giving them to every ticker of the CIK (tried first,
   reverted) handed a bank's ETNs and preferreds the bank's market cap.
2. The retry time counted companies whose tags the parser cannot read (PARSE_GAP, 7 days) — pressing again after it
   changes nothing; only transient failures have a retry time.
3. A parser update retries those companies at once instead of after their back-off.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from marketlens.application.data_access import FUNDAMENTALS
from marketlens.application.sync import MarketSync
from tests import live_fixtures as LF
from tests.integration.test_background_sync import _join, _live


def test_a_companys_shares_go_to_its_primary_ticker_not_to_its_notes_or_etns(tmp_path, monkeypatch):
    # diagnosis 2026-09-28: handing the shares to EVERY ticker of a CIK gave BMO's ETNs (FNGU, BULZ) and Huntington's
    # preferreds the issuer's market cap; the SEC file lists the primary equity first
    etn = {**LF.COMPANIES["NVDA"], "name": "NVIDIA CORP 2X ETN", "px": 40.0}
    monkeypatch.setattr(LF, "COMPANIES", {**LF.COMPANIES, "NVDU": etn})
    svc = _live(tmp_path)
    MarketSync(svc.registry, svc.store).run(LF.NOW)
    caps = {s.ticker: s.market_cap for s in svc.store.securities(None)}
    assert caps.get("NVDA") is not None and caps["NVDA"] > 1e9
    assert caps.get("NVDU") is None  # never the issuer's market cap on its note


def test_a_parse_gap_has_no_retry_time_but_a_transient_failure_does(tmp_path):
    svc = _live(tmp_path)
    svc.start_sync()
    _join()
    now = svc.now()
    for t in ("NVDA", "JPM"):
        svc.store.record_ingestion(FUNDAMENTALS, t, now, "OK", rows=1)
    svc.store.record_ingestion(FUNDAMENTALS, "TSM", now, "PARSE_GAP", "not supported: 분기 10-Q/10-K 데이터 없음")
    state = {"status": "DONE"}
    svc._settle_sync(state)
    assert "retry_at" not in state  # a parse gap waits for an app update, not for a time
    svc.store.record_ingestion(FUNDAMENTALS, "JPM", now, "FAILED", "timeout")
    state = {"status": "DONE"}
    svc._settle_sync(state)
    if state.get("status") in ("INCOMPLETE", "NEEDS_SETUP"):
        assert state.get("retry_at")


def test_a_new_parser_retries_parse_gaps_at_once(tmp_path):
    svc = _live(tmp_path)
    now = svc.now()
    svc.store.record_ingestion(FUNDAMENTALS, "NVDA", now, "PARSE_GAP", "old parser")
    assert svc.store.ingestion(FUNDAMENTALS, "NVDA").next_attempt_at is not None
    svc.store.set_setting("sec_parser_version", "sec-parser-1")
    MarketSync(svc.registry, svc.store).run(now + timedelta(hours=1))
    m = svc.store.ingestion(FUNDAMENTALS, "NVDA")
    assert m.status == "OK"  # read again at once by the new parser (not after 7 days)
    assert svc.store.get_setting("sec_parser_version") == "sec-parser-2"


def test_an_annual_only_20f_us_gaap_filer_is_a_foreign_issuer_not_a_parse_gap():
    from marketlens.application.data_access import _failure_status
    from marketlens.providers.contracts import NotSupported
    from marketlens.providers.live.sec_edgar import parse_company_facts

    fy = {"val": 1e10, "start": "2025-01-01", "end": "2025-12-31", "filed": "2026-04-20", "form": "20-F", "fy": 2025, "fp": "FY"}
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [fy]}}, "NetIncomeLoss": {"units": {"USD": [dict(fy, val=1e9)]}}}}}
    with pytest.raises(NotSupported) as e:
        parse_company_facts(facts, "TM")
    assert "20-F/40-F" in str(e.value)
    assert _failure_status(f"not supported: {e.value}") == "NOT_SUPPORTED"  # before: PARSE_GAP ("재무 태그 해석 불가")


def test_a_40f_filer_buried_under_hundreds_of_prospectuses_is_foreign():
    from marketlens.providers.live.sec_edgar import parse_submissions_profile

    forms = ["424B2"] * 600 + ["40-F"] + ["424B2"] * 300
    prof = parse_submissions_profile({"sic": 6021, "filings": {"recent": {"form": forms}}, "addresses": {"business": {"stateOrCountryDescription": "Ontario"}}})
    assert prof["foreign_issuer"] is True and prof["country"] == "Ontario"


def test_readiness_leaves_filing_confirmed_foreign_issuers_out_of_the_filings_denominator(tmp_path):
    svc = _live(tmp_path)
    svc.start_sync()
    _join()
    st = svc.store
    big = [s for s in st.securities(None) if (s.market_cap or 0) >= 1e9]
    assert big
    t = big[0].ticker
    with st.sf() as s:
        from marketlens.infrastructure.db.models import FundamentalVintageRow

        s.query(FundamentalVintageRow).filter(FundamentalVintageRow.ticker == t).delete()
        s.commit()
    st.record_ingestion(FUNDAMENTALS, t, svc.now(), "NOT_SUPPORTED", f"{t}: 20-F/40-F 외국 발행사 — 연간 us-gaap만 있고 분기 10-Q/10-K 없음")
    stats = st.coverage_stats(LF.NOW.date(), 1e9)
    assert stats["large_fund_not_supported"] >= 1 and stats["large_fund_parse_gap"] == 0


# ---------------------------------------------------------------- measured 2026-09-28: the denominators counted non-stocks
DIRECTORY_NASDAQ = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
ABCW|ABC Acquisition Corp - Warrant|S|N|N|100|N|N
ABCU|ABC Acquisition Corp - Unit|S|N|N|100|N|N
QQQ|Invesco QQQ Trust, Series 1|G|N|N|100|Y|N
ZZZZ|Test Co - Common Stock|Q|Y|N|100|N|N
TSM2|Some Co - American Depositary Shares|Q|N|N|100|N|N
File Creation Time: 0925202621:31|||||||"""
DIRECTORY_OTHER = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
BRK.B|Berkshire Hathaway Inc. Class B Common Stock|N|BRK.B|N|100|N|BRK=B
BAC-L|Bank of America Corp 7.25% Non-Cumulative Perpetual Convertible Preferred Stock, Series L|N|BAC-L|N|100|N|BAC-L
PFH|Prudential Financial 4.125% Junior Subordinated Notes due 2060|N|PFH|N|100|N|PFH
MS-A|Morgan Stanley Depositary Shares, each representing 1/1000th of a Share of Floating Rate Series A Preferred|N|MS-A|N|100|N|MS-A
File Creation Time: 0925202621:31||||||"""


def test_the_symbol_directory_tells_stocks_from_other_listings():
    from marketlens.providers.live.nasdaq_symbols import canonical, parse_directory

    k = parse_directory(DIRECTORY_NASDAQ, "Symbol") | parse_directory(DIRECTORY_OTHER, "ACT Symbol")
    assert k["AAPL"] == "common" and k["TSM2"] == "common" and k[canonical("BRK-B")] == "common"
    assert k["ABCW"] == "warrant" and k["ABCU"] == "unit" and k["QQQ"] == "etf"
    assert k[canonical("BAC-L")] == "preferred" and k[canonical("MS-A")] == "preferred" and k["PFH"] == "note"
    assert "ZZZZ" not in k  # test issues are not listings


def test_readiness_counts_stocks_and_says_what_it_left_out(tmp_path):
    import json

    from marketlens.application.readiness import evaluate

    svc = _live(tmp_path)
    svc.start_sync()
    _join()
    st = svc.store
    assert st.instrument_kinds()  # the sync stored the directory
    kinds = st.instrument_kinds()
    kinds["TINY"] = "preferred"  # pretend one listing is a preferred: it leaves the denominators
    st.set_setting("instrument_kinds", json.dumps(kinds))
    stats = st.coverage_stats(LF.NOW.date(), 1e9)
    assert stats["listed_non_stock"] == 1 and stats["listed"] == stats["listed_all"] - 1
    stats["bars_60"] = 0  # force the reason to be shown
    rd = evaluate("LIVE", svc.registry, stats, None)
    assert any("우선주·워런트·유닛·채권·펀드 1종목 제외" in r for r in rd.scanner_reasons)


def test_a_share_class_meets_its_prices_under_one_spelling(monkeypatch):
    import httpx

    from marketlens.providers.live.sec_edgar import SecEdgarProvider

    def handler(req):  # noqa: ANN001, ANN202
        return httpx.Response(200, json={"fields": ["cik", "name", "ticker", "exchange"],
                                         "data": [[1067983, "BERKSHIRE HATHAWAY INC", "BRK-B", "NYSE"], [1067983, "BERKSHIRE HATHAWAY INC", "BRK-A", "NYSE"]]})

    sec = SecEdgarProvider("MarketLens test test@example.com", transport=httpx.MockTransport(handler))
    assert [s.ticker for s in sec.list_securities()] == ["BRK.B", "BRK.A"]  # the exchanges' / Polygon's spelling
    assert sec.cik_for("BRK.B") == sec.cik_for("BRK-B") == 1067983


def test_shares_come_from_the_balance_sheet_when_no_cover_page_frame_has_the_company():
    import httpx

    from marketlens.providers.live.sec_edgar import SecEdgarProvider

    def handler(req):  # noqa: ANN001, ANN202
        p = req.url.path
        if "/dei/EntityCommonStockSharesOutstanding/" in p:
            return httpx.Response(200, json={"data": [{"cik": 1, "val": 100.0, "end": "2026-07-25"}]})
        if "/us-gaap/CommonStockSharesOutstanding/" in p:
            return httpx.Response(200, json={"data": [{"cik": 1, "val": 90.0, "end": "2026-06-30"}, {"cik": 2, "val": 50.0, "end": "2026-06-30"}]})
        return httpx.Response(404)

    from datetime import date

    got = SecEdgarProvider("MarketLens test test@example.com", transport=httpx.MockTransport(handler)).shares_outstanding_all(date(2026, 9, 25))
    assert got[1] == (100.0, date(2026, 7, 25))  # the cover page wins
    assert got[2] == (50.0, date(2026, 6, 30))  # a company missing from the cover-page frames is not left without shares


# ---------------------------------------------------------------- owner report 2026-09-28 (new build): 89 % / 70 % / 79 %
def test_readiness_gates_count_what_the_scanner_can_use(tmp_path):
    """Market cap / sector / filings: the stocks at the scanner's price and dollar-volume limits (the sync fetches filings
    only for those; the scanner drops an illiquid stock whatever its market cap). Price history: a stock listed fewer
    than 60 sessions ago is counted apart."""
    from datetime import date

    from marketlens.application.readiness import evaluate
    from marketlens.domain.enums import Exchange
    from marketlens.domain.market import Bar, Security
    from marketlens.domain.market_calendar import add_trading_days
    from tests.acceptance_a_grade.test_research_integrity import _store

    st = _store(tmp_path)
    today = date(2026, 9, 25)
    days = [add_trading_days(date(2026, 3, 2), i) for i in range(145)]
    days = [d for d in days if d <= today]
    st.sync_universe([Security(t, t, Exchange.NASDAQ, "Tech", "x", None, cik=i) for i, t in enumerate(("LIQ", "THIN", "NEW"), 1)], today)
    for d in days:
        bars = {"LIQ": Bar(d, 50, 51, 49, 50, 1e6), "THIN": Bar(d, 50, 51, 49, 50, 1e3)}  # $50M vs $50K a day
        if d >= days[-20]:
            bars["NEW"] = Bar(d, 20, 21, 19, 20, 5e6)  # listed 20 sessions ago
        st.save_grouped(d, bars, "polygon")
    st.set_shares({"LIQ": (1e8, today)})  # THIN has no market cap: it does not count against the gate
    st.refresh_market_caps()
    stats = st.coverage_stats(today, 1e9, 2e7, 5.0)
    assert stats["young"] == 1 and stats["bars_60"] == 2  # NEW is counted apart, not as missing history
    assert stats["liquid"] == 2 and stats["liquid_with_market_cap"] == 1  # LIQ and NEW are liquid; THIN is not
    from marketlens.application.registry import build_live_registry
    from marketlens.config import Settings
    from marketlens.domain.enums import DataMode

    reg = build_live_registry(Settings(mode=DataMode.LIVE, database_url="sqlite:///:memory:", sec_user_agent="t t@example.com", llm_provider="none"))
    rd = evaluate("LIVE", reg, {**stats, "market_days": 145}, None)
    assert rd.progress["price_history"] == 1.0 and rd.progress["market_cap"] == 0.5
    assert any("스캐너가 쓸 수 있는 주식 2종목 기준" in r for r in rd.scanner_reasons)


def test_liquid_stocks_the_frames_missed_get_their_shares_company_by_company(tmp_path, monkeypatch):
    from marketlens.application.sync import _find

    svc = _live(tmp_path)
    sec = _find(svc.registry, "universe", "shares_outstanding_all")
    monkeypatch.setattr(sec, "shares_outstanding_all", lambda _d: {})  # the quarterly frames have nobody
    asked = []

    def one(t, as_of):  # noqa: ANN001, ANN202
        asked.append(t)
        return (LF.COMPANIES[t]["shares"], as_of)

    monkeypatch.setattr(sec, "shares_outstanding_of", one)
    rep = MarketSync(svc.registry, svc.store).run(LF.NOW)
    caps = {s.ticker: s.market_cap for s in svc.store.securities(None)}
    assert caps["NVDA"] and caps["JPM"]  # read company by company
    assert "TINY" not in asked  # illiquid: never asked
    assert rep.shares_looked_up >= 2
    again = MarketSync(svc.registry, svc.store).run(LF.NOW)
    assert again.shares_looked_up == 0  # nothing is asked twice


def test_company_shares_come_from_the_cover_page_else_the_balance_sheet_and_never_stale():
    from datetime import date

    import httpx

    from marketlens.providers.contracts import NotSupported
    from marketlens.providers.live.sec_edgar import SecEdgarProvider

    def handler(req):  # noqa: ANN001, ANN202
        p = req.url.path
        if p.endswith("company_tickers_exchange.json"):
            return httpx.Response(200, json={"fields": ["cik", "name", "ticker", "exchange"], "data": [[1, "A", "AAA", "Nasdaq"], [2, "B", "BBB", "Nasdaq"], [3, "C", "CCC", "Nasdaq"]]})
        if "CIK0000000001/dei/" in p:
            return httpx.Response(200, json={"units": {"shares": [{"end": "2026-07-20", "val": 9e8, "filed": "2026-08-01"}, {"end": "2026-04-20", "val": 8e8, "filed": "2026-05-01"}]}})
        if "CIK0000000002/us-gaap/CommonStockSharesOutstanding" in p:
            return httpx.Response(200, json={"units": {"shares": [{"end": "2026-06-30", "val": 5e8, "filed": "2026-08-05"}]}})
        if "CIK0000000003/dei/" in p:
            return httpx.Response(200, json={"units": {"shares": [{"end": "2023-01-20", "val": 1e8, "filed": "2023-02-01"}]}})
        return httpx.Response(404)

    sec = SecEdgarProvider("MarketLens test test@example.com", transport=httpx.MockTransport(handler))
    assert sec.shares_outstanding_of("AAA", date(2026, 9, 25)) == (9e8, date(2026, 7, 20))
    assert sec.shares_outstanding_of("BBB", date(2026, 9, 25)) == (5e8, date(2026, 6, 30))
    with pytest.raises(NotSupported):
        sec.shares_outstanding_of("CCC", date(2026, 9, 25))  # three years old: never used as today's shares
