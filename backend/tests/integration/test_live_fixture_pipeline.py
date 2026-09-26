"""LIVE path on recorded-shape fixtures: free providers → local point-in-time store → scanner → decision.

The provider classes, router, store, scanner and pipeline are the production code; only the network is a
``httpx.MockTransport`` returning payloads in the exact shape of SEC / Polygon / Finnhub / FRED / FINRA.
This guards against "passes in MOCK, breaks in LIVE".
"""

from __future__ import annotations

import pytest

from marketlens.application.registry import build_live_registry
from marketlens.application.services import MarketLensService
from marketlens.config import Settings
from marketlens.domain.enums import Action, DataMode, DataQuality
from marketlens.infrastructure.db import repository as repo
from marketlens.infrastructure.db.models import Base
from marketlens.infrastructure.db.session import make_engine, make_session_factory
from marketlens.providers.llm.base import UnavailableLLM
from tests.live_fixtures import NOW, live_transport


@pytest.fixture(scope="module")
def live():
    seen: list[str] = []
    st = Settings(mode=DataMode.LIVE, database_url="sqlite:///:memory:", sec_user_agent="MarketLens test test@example.com",
                  finnhub_api_key="fixture-key", fred_api_key="fixture-key", polygon_api_key="fixture-key", llm_provider="none")
    reg = build_live_registry(st, transport=live_transport(seen), sleep=lambda _s: None)
    eng = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    svc = MarketLensService(st, make_session_factory(eng), registry=reg, llm=UnavailableLLM(), now_fn=lambda: NOW)
    sync = svc.sync_market(max_bar_calls=250, max_profiles=10)
    scan = svc.run_scan(run_committee=True)
    return svc, sync, scan, seen


def test_sync_builds_market_caps_and_sectors_from_free_sources(live):
    svc, sync, _scan, _seen = live
    assert sync["status"] == "SYNC_COMPLETE", sync["errors"]
    assert sync["splits_new"] == 1 and sync["bar_days_remaining"] == 0
    assert sync["bar_days_loaded"] >= 60 and sync["shares_updated"] >= 3
    secs = {s.ticker: s for s in svc.store.securities(None)}
    assert "OTCX" not in secs  # OTC names are out of scope
    for t in ("NVDA", "JPM", "TSM"):
        assert secs[t].market_cap is not None and secs[t].market_cap > 1e10  # SEC shares × stored close
    assert secs["NVDA"].industry == "Semiconductors" and secs["TSM"].industry == "Semiconductors"
    assert secs["JPM"].sector == "Financial Services" and secs["JPM"].industry.startswith("Banks")
    assert secs["TSM"].is_adr  # 20-F filer → foreign issuer


def test_live_scanner_universe_is_not_dropped_as_market_cap_unknown(live):
    svc, _sync, scan, _seen = live
    with svc.sf() as s:
        run = repo.latest_scan(s, mode="LIVE")
        stage1 = run.stages[0]
        assert stage1["output_count"] >= 3, stage1  # NVDA, JPM, TSM pass eligibility
        assert "시가총액 미상" not in stage1["note"]
        tickers = {r.ticker for r in repo.recommendations_for_scan(s, run.id)}
    assert {"NVDA", "JPM", "TSM"} <= tickers and "TINY" not in tickers  # TINY: market cap below minimum
    assert scan.candidates >= 3


def test_live_recommendation_uses_real_data_states(live):
    svc, _sync, _scan, _seen = live
    with svc.sf() as s:
        nvda = repo.latest_recommendation(s, "NVDA", mode="LIVE")
        tsm = repo.latest_recommendation(s, "TSM", mode="LIVE")
    res = nvda.result
    checks = {c["data_type"]: c["quality"] for c in res["data_quality"]["checks"]}
    assert nvda.mode == "LIVE" and nvda.price_source == "finnhub"
    assert checks["price"] == DataQuality.DELAYED.value  # free Finnhub quotes are not guaranteed real-time
    assert checks["fundamentals"] == "FRESH" and checks["price_history"] == "FRESH" and checks["macro"] == "FRESH"
    assert checks["analyst"] == "MISSING" and checks["options"] == "MISSING"  # paid data → honestly missing
    assert checks["short_interest"] == "FRESH" and res["short_interest_pct"] == pytest.approx(0.012, rel=1e-3)  # FINRA ÷ SEC shares
    # without forward estimates the semiconductor valuation model (forward P/E, PEG = 6 of 8 weight) cannot judge
    # the price → "unknown", never "bad": no BUY and no SELL (audit P0: missing sector data became REDUCE)
    assert nvda.deterministic_action == Action.DATA_INSUFFICIENT.value
    assert "INSUFFICIENT_MODEL_COVERAGE" in res["decision"]["vetoes"] and "STALE_PRICE" not in res["decision"]["vetoes"]
    assert res["sector_model_id"] == "semiconductor" and res["multiples"]["market_cap"] is not None
    # TSM files IFRS 20-F reports: no us-gaap quarterly facts → fundamentals missing → no recommendation
    assert tsm.deterministic_action == Action.DATA_INSUFFICIENT.value
    assert tsm.result["sector_model_id"] == "semiconductor"


def test_market_and_company_news_use_the_right_paths_and_relevance(live):
    svc, _sync, _scan, seen = live
    assert any("finnhub.io/api/v1/news?" in u and "category=general" in u for u in seen)  # tickers=None → market news
    assert any("/company-news?" in u and "symbol=NVDA" in u for u in seen)  # candidates → company news
    ctx = svc.last_scan_context
    titles = {i.title for i in ctx.issues.issues}
    assert "Nvidia raises full-year outlook" in titles
    arts = svc._external_news(next(r for r in [svc.analyze("NVDA", persist=False)[0]]), ctx)
    texts = " ".join(t for _src, t in arts)
    assert "Nvidia raises full-year outlook" in texts
    assert "Five dividend stocks" not in texts and "Stocks to watch" not in texts  # irrelevant → never shown to the AI


def test_live_committee_without_llm_is_unavailable_not_mock(live):
    svc, _sync, _scan, _seen = live
    with svc.sf() as s:
        run = repo.latest_scan(s, mode="LIVE")
        statuses = {r.committee_status for r in repo.recommendations_for_scan(s, run.id)}
    assert statuses <= {"UNAVAILABLE", "SKIPPED"}
