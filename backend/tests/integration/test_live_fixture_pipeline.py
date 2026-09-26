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
                  finnhub_api_key="fixture-key", fred_api_key="fixture-key", polygon_api_key="fixture-key", alphavantage_api_key="fixture-key", llm_provider="none")
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
    assert checks["options"] == "MISSING"  # no free options source → honestly missing
    assert checks["short_interest"] == "FRESH" and res["short_interest_pct"] == pytest.approx(0.012, rel=1e-3)  # FINRA ÷ SEC shares
    # free consensus: Alpha Vantage FY1/FY2 (final candidate) + Finnhub calendar snapshot
    an = res["analyst"]
    assert checks["analyst"] == "FRESH" and an["source"] == "alphavantage+finnhub"
    assert an["forward_eps"] == pytest.approx(1.82 * (128 / 365) + 2.21 * (1 - 128 / 365), rel=1e-3) and an["forward_eps_basis"].startswith("NTM")
    assert an["eps_revision_30d"] == pytest.approx(1.82 / 1.76 - 1, rel=1e-6) and an["revision_basis"]["90d"] == "PROVIDER"
    assert an["analyst_count"] == 42 and an["estimate_dispersion"] is None  # no stdev published → not invented
    assert an["cross_check"].startswith("CONSISTENT")  # AV current quarter 0.47 vs Finnhub calendar 0.47
    assert svc.last_scan_context.estimate_fetch["NVDA"] == "OK"
    # with a forward consensus the valuation model can judge the price → a real decision, not DATA INSUFFICIENT
    assert nvda.deterministic_action != Action.DATA_INSUFFICIENT.value
    assert "INSUFFICIENT_MODEL_COVERAGE" not in res["decision"]["vetoes"]
    # SEC 8-K Exhibit 99.1 guidance, attached to the report released with it; no pre-release consensus snapshot
    # exists in the store (history starts at the first sync) → the comparison is not made, nothing is invented
    rep = next(e for e in nvda.inputs["earnings"] if e["report_date"] == "2026-07-28")
    g = rep["guidance"]
    assert g["next_q_revenue_low"] == pytest.approx(32.34e9) and g["next_q_revenue_high"] == pytest.approx(33.66e9)
    assert g["gross_margin_guide"] == pytest.approx(0.744) and g["next_q_revenue_consensus"] is None
    assert "plus or minus 2%" in g["evidence"] and g["source"].endswith("nvda-ex991.htm") and g["confidence"] == "MEDIUM"
    # JPM: Alpha Vantage has no rows → only the calendar snapshot; revisions are ACCUMULATING, never invented
    jpm = next(r for r in [repo.latest_recommendation(svc.sf(), "JPM", mode="LIVE")] if r is not None)
    ja = jpm.result["analyst"]
    assert ja["forward_eps"] is None and ja["source"] == "finnhub"
    assert ja["revision_status"]["30d"].startswith("ACCUMULATING") and ja["eps_revision_30d"] is None
    # bank KPIs from SEC XBRL (loans, deposits, provisions, charge-offs, tangible equity); CET1 is not tagged
    # in this filing and NIM needs data SEC XBRL does not standardise → both stay missing, never estimated
    jf = jpm.result["features"]
    assert jf["rotce"] is not None and jf["loan_growth"] is not None and jf["charge_off_rate"] is not None
    assert jf.get("cet1") is None and jf.get("nim") is None
    assert jpm.result["fundamental_rules"]["critical_missing"] == [] and jpm.result["multiples"]["p_tbv"] is not None
    assert res["sector_model_id"] == "semiconductor" and res["multiples"]["market_cap"] is not None
    # TSM files IFRS 20-F reports: annual IFRS facts only → ANNUAL_ONLY, no quarterly data, no ADR ratio →
    # currency-free ratios are shown, per-share valuation is not computed, and no recommendation is made
    assert tsm.deterministic_action == Action.DATA_INSUFFICIENT.value
    tchecks = {c["data_type"]: c for c in tsm.result["data_quality"]["checks"]}
    assert "ANNUAL_ONLY" in tchecks["fundamentals"]["reason_ko"] and "QUARTERLY_DATA_UNAVAILABLE" in tchecks["fundamentals"]["reason_ko"]
    assert tsm.result["features"]["revenue_growth_yoy"] == pytest.approx(3.81 / 2.894 - 1, rel=1e-6)
    assert tsm.result["multiples"] is None or tsm.result["multiples"].get("trailing_pe") is None
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
