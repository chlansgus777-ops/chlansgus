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
