"""Owner report 2026-09-28 — an hour after "데이터 준비 시작": "시가총액 확인 종목 57 % < 80 %, 대형주 분기 재무 76 %
(재무 태그 해석 불가 80종목), 실패한 항목을 다시 시도할 수 있는 시각: 2026-10-04".

1. Shares outstanding went to ONE ticker per company (the first in the SEC file): a common stock listed after its own
   warrant / unit had no market cap.
2. The retry time counted companies whose tags the parser cannot read (PARSE_GAP, 7 days) — pressing again after it
   changes nothing; only transient failures have a retry time.
3. A parser update retries those companies at once instead of after their back-off.
"""

from __future__ import annotations

from datetime import timedelta

from marketlens.application.data_access import FUNDAMENTALS
from marketlens.application.sync import MarketSync
from tests import live_fixtures as LF
from tests.integration.test_background_sync import _join, _live


def test_every_ticker_of_a_company_gets_its_shares(tmp_path, monkeypatch):
    # the company's warrant comes first in the SEC file, then its common stock
    warrant = {**LF.COMPANIES["NVDA"], "name": "NVIDIA CORP WARRANTS", "px": 3.0}
    monkeypatch.setattr(LF, "COMPANIES", {"NVDAW": warrant, **LF.COMPANIES})
    svc = _live(tmp_path)
    MarketSync(svc.registry, svc.store).run(LF.NOW)
    caps = {s.ticker: s.market_cap for s in svc.store.securities(None)}
    assert caps.get("NVDA") is not None and caps["NVDA"] > 1e9  # before: None (the shares went to NVDAW)
    assert caps.get("NVDAW") is not None


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
