"""A-grade acceptance: sync ≠ ready; not-ready is never shown as "no opportunities"; the gate is honest."""

from __future__ import annotations

from fastapi.testclient import TestClient

from marketlens.api.app import create_app
from tests.integration.test_live_fixture_pipeline import live  # noqa: F401  (module fixture)
from tests.integration.test_service_api import make_service


def test_mock_mode_is_paper_only():
    r = make_service(universe=40).readiness()
    assert r["recommendation_readiness"] == "PAPER ONLY" and r["scanner_status"] == "NOT_APPLICABLE"


def test_live_fixture_after_a_complete_sync_is_limited_not_full(live):  # noqa: F811
    svc, _sync, _scan, _seen = live
    r = svc.readiness()
    assert r["sync"]["status"] == "SYNC_COMPLETE" and r["scanner_status"] == "SCANNER_READY"
    # free data: options/13F unavailable, revisions accumulating, providers not live-verified → LIMITED
    assert r["recommendation_readiness"] == "LIMITED"
    cats = {c["category"]: c["status"] for c in r["categories"]}
    assert cats["옵션(IV·예상 변동폭)"] == "UNAVAILABLE" and cats["추정치 리비전"].startswith("ACCUMULATING")
    assert all("NOT_LIVE_VERIFIED" in v for k, v in cats.items() if v not in ("UNAVAILABLE", "BLOCKED_BY_CREDENTIAL"))


def test_initial_partial_sync_is_not_ready_and_the_api_says_why():
    """Audit P1: sync reported OK after 30 days, the scan then found 0 candidates and looked like 'no ideas'."""
    from marketlens.application.registry import build_live_registry
    from marketlens.application.services import MarketLensService
    from marketlens.config import Settings
    from marketlens.domain.enums import DataMode
    from marketlens.infrastructure.db.models import Base
    from marketlens.infrastructure.db.session import make_engine, make_session_factory
    from marketlens.providers.llm.base import UnavailableLLM
    from tests.live_fixtures import NOW, live_transport

    st = Settings(mode=DataMode.LIVE, database_url="sqlite:///:memory:", sec_user_agent="MarketLens test test@example.com",
                  finnhub_api_key="k", fred_api_key="k", polygon_api_key="k", llm_provider="none")
    eng = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    svc = MarketLensService(st, make_session_factory(eng), registry=build_live_registry(st, transport=live_transport(), sleep=lambda _s: None),
                            llm=UnavailableLLM(), now_fn=lambda: NOW)
    sync = svc.sync_market(max_bar_calls=20, max_profiles=10)  # a first, budget-limited sync
    assert sync["status"] == "SYNC_PARTIAL" and sync["bar_days_remaining"] > 0
    r = svc.readiness()
    assert r["scanner_status"] == "SCANNER_NOT_READY" and r["recommendation_readiness"] == "NOT READY"
    assert any("60거래일" in x for x in r["scanner_reasons"]) and r["progress"]["market_days"] < 1
    with TestClient(create_app(st, service=svc, run_migrations=False)) as c:
        body = c.get("/api/opportunities").json()
    assert body["rows"] == [] and body["readiness"]["scanner_status"] == "SCANNER_NOT_READY"  # the UI can explain the empty list
