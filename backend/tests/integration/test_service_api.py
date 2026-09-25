"""End-to-end: MOCK service + API with an in-memory database; LIVE without keys never shows mock data."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from marketlens.api.app import create_app
from marketlens.application.evaluation_service import EvaluationService
from marketlens.application.registry import build_live_registry, build_mock_registry
from marketlens.application.services import MarketLensService
from marketlens.config import Settings
from marketlens.domain.enums import DataMode
from marketlens.infrastructure.db.models import Base
from marketlens.infrastructure.db.session import make_engine, make_session_factory
from marketlens.providers.llm.base import UnavailableLLM
from marketlens.providers.llm.mock_llm import MockLLMProvider
from tests.conftest import NOW


class CountingLLM(MockLLMProvider):
    pass


def make_service(mode=DataMode.MOCK, llm=None, universe=200):
    eng = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    st = Settings(mode=mode, database_url="sqlite:///:memory:", sec_user_agent=None, llm_provider="mock" if mode == DataMode.MOCK else "none")
    reg = build_mock_registry(now=NOW, universe_size=universe) if mode == DataMode.MOCK else build_live_registry(st)
    clock = {"t": NOW}
    svc = MarketLensService(st, make_session_factory(eng), registry=reg, llm=llm or (MockLLMProvider() if mode == DataMode.MOCK else UnavailableLLM()), now_fn=lambda: clock["t"])
    svc._clock = clock  # type: ignore[attr-defined]
    return svc


@pytest.fixture(scope="module")
def mock_svc():
    svc = make_service(llm=CountingLLM())
    svc.run_scan()
    return svc


def test_scan_persists_audit_trail(mock_svc):
    from marketlens.infrastructure.db import repository as repo

    with mock_svc.sf() as s:
        scan = repo.latest_scan(s)
        recs = repo.recommendations_for_scan(s, scan.id)
        assert scan.stages[0]["input_count"] == 200 and len(recs) > 0
        r = recs[0]
        for f in ("scoring_model_version", "decision_model_version", "agent_prompt_version", "provider_version", "config_version", "schema_version", "input_fingerprint"):
            assert getattr(r, f)
        assert r.mode == "MOCK" and r.inputs and r.result and r.model_config_snapshot["scoring_model.toml"]
        assert r.price_source == "mock" and r.session
        assert repo.committee_for(s, r.id) is not None


def test_llm_only_on_top_candidates(mock_svc):
    from marketlens.infrastructure.db import repository as repo

    top_n = mock_svc.base_cfg.scanner.ai_committee_top_n
    with mock_svc.sf() as s:
        scan = repo.latest_scan(s)
        recs = repo.recommendations_for_scan(s, scan.id)
        with_committee = [r for r in recs if repo.committee_for(s, r.id) is not None]
    assert len(with_committee) == min(top_n, len(recs))
    per_committee_calls = 7 + 4 + 3
    assert mock_svc.llm.calls <= top_n * per_committee_calls  # never proportional to the universe (200)


def test_replay_matches(mock_svc):
    from marketlens.infrastructure.db import repository as repo

    with mock_svc.sf() as s:
        rid = repo.recommendations_for_scan(s, repo.latest_scan(s).id)[0].id
    o = mock_svc.replay_recommendation(rid)
    assert o.matches and o.fingerprint_matches


def test_rescan_with_unchanged_data_is_stable(mock_svc):
    from marketlens.infrastructure.db import repository as repo

    with mock_svc.sf() as s:
        first = {r.ticker: r.deterministic_action for r in repo.recommendations_for_scan(s, repo.latest_scan(s).id)}
    mock_svc.run_scan(run_committee=False)
    with mock_svc.sf() as s:
        recs = repo.recommendations_for_scan(s, repo.latest_scan(s).id)
        for r in recs:
            # compared against the previous analysis: nothing changed → no "initial" marker, no flip
            assert all(c["kind"] != "initial" for c in r.result["changes"])
            if r.ticker in first:
                assert r.deterministic_action == first[r.ticker]


def test_api_endpoints_and_mock_banner(mock_svc):
    app = create_app(mock_svc.settings, service=mock_svc, run_migrations=False)
    with TestClient(app) as c:
        sys = c.get("/api/system").json()
        assert sys["mode"] == "MOCK" and sys["mock_banner"] is True
        opp = c.get("/api/opportunities").json()
        assert opp["rows"] and all(r["mode"] == "MOCK" for r in opp["rows"])
        row = opp["rows"][0]
        for k in ("rank", "ticker", "company", "sector", "price", "session", "score", "confidence", "action", "ideal_entry", "max_buy", "target", "downside", "rr", "catalyst", "risk", "data_quality"):
            assert k in row
        d = c.get(f"/api/stocks/{row['ticker']}").json()
        assert d["analysis"]["scorecard"]["components"] and d["versions"]["scoring"]
        assert c.get("/api/dashboard").status_code == 200
        assert c.get("/api/issues").json()["available"]
        assert c.get("/api/macro").json()["available"]
        assert c.get("/api/health").status_code == 200
        settings = c.get("/api/settings").json()
        assert "ANTHROPIC_API_KEY" in settings["keys_configured"] and not any(isinstance(v, str) and len(v) > 20 for v in settings["keys_configured"].values())
        assert c.put("/api/portfolio", json={"cash": 50000, "holdings": [{"ticker": "NVDA", "quantity": 10, "cost_basis": 100}]}).json()["cash"] == 50000
        assert c.post("/api/watchlist/NVDA").status_code == 200 and c.get("/api/watchlist").json()[0]["ticker"] == "NVDA"
        rid = row["id"]
        assert c.get(f"/api/recommendations/{rid}/replay").json()["matches"]
        assert c.get("/api/recommendations/999999/replay").status_code == 404


def test_paper_and_outcomes_flow():
    svc = make_service(universe=120)
    world = svc.registry.world
    for w in (6, 4, 2):
        svc._clock["t"] = NOW - timedelta(weeks=w)
        world.set_now(svc._clock["t"])
        svc.data.cache = type(svc.data.cache)()
        svc.run_scan(run_committee=False)
    svc._clock["t"] = NOW
    world.set_now(NOW)
    svc.data.cache = type(svc.data.cache)()
    ev = EvaluationService(svc)
    assert ev.update_outcomes() > 0
    counts = ev.update_paper()
    assert counts["opened"] > 0
    perf = ev.performance()
    assert perf["samples"] > 0 and perf["paper"]["trades"] > 0
    assert ev.update_outcomes() == 0  # idempotent: outcomes are written once
    cal = ev.calibrate()
    assert cal["status"] == "INSUFFICIENT_SAMPLES"  # never changes production weights without enough data
    assert svc.model_config().scoring_model.version == svc.base_cfg.scoring_model.version


def test_live_mode_without_keys_shows_missing_not_mock():
    svc = make_service(mode=DataMode.LIVE)
    assert all(p.mode == DataMode.LIVE for ch in svc.registry.chains.values() for p in ch.providers)
    summary = svc.run_scan()
    assert summary.candidates == 0
    f = svc.data.quote("NVDA")
    assert f.value is None and f.error
    app = create_app(svc.settings, service=svc, run_migrations=False)
    with TestClient(app) as c:
        assert c.get("/api/system").json()["mock_banner"] is False
        assert c.get("/api/macro").json()["available"] is False
        assert c.get("/api/opportunities").json()["rows"] == []


def test_mock_llm_refused_in_live_mode():
    from marketlens.application.services import build_llm

    st = Settings(mode=DataMode.LIVE, database_url="sqlite:///:memory:", sec_user_agent=None, llm_provider="mock")
    llm = build_llm(st)
    assert not llm.available


def test_universe_and_history_are_persisted(mock_svc):
    from sqlalchemy import func, select

    from marketlens.infrastructure.db.models import FundamentalVintageRow, PriceBarRow, SecurityRow

    with mock_svc.sf() as s:
        assert s.scalar(select(func.count()).select_from(SecurityRow)) == 200
        assert s.scalar(select(func.count()).select_from(FundamentalVintageRow)) > 0
        assert s.scalar(select(func.count()).select_from(PriceBarRow)) > 0
