"""The installed app had no way to prepare its LIVE data: the readiness screen said "run a data sync" but only the
CLI (`marketlens sync`) or a hand-made POST could, so a user stayed at NOT READY forever. Now the screen starts a
background job (``POST /api/sync/start``) that repeats the bounded sync until the data is complete or a round
makes no progress, and reports its progress (``GET /api/sync/status``)."""

from __future__ import annotations

import threading

import pytest

from marketlens.application.registry import build_live_registry
from marketlens.application.services import MarketLensService
from marketlens.config import Settings
from marketlens.domain.enums import DataMode
from marketlens.infrastructure.db.models import Base
from marketlens.infrastructure.db.session import make_engine, make_session_factory
from marketlens.providers.llm.base import UnavailableLLM
from tests.live_fixtures import NOW, live_transport

KEYS = {"sec_user_agent": "MarketLens test test@example.com", "finnhub_api_key": "fixture-key", "fred_api_key": "fixture-key",
        "polygon_api_key": "fixture-key", "alphavantage_api_key": "fixture-key"}


def _live(tmp_path, **over):  # noqa: ANN001, ANN003, ANN202
    keys = {**KEYS, **over}
    st = Settings(mode=DataMode.LIVE, database_url="sqlite:///x", llm_provider="none", **keys)
    reg = build_live_registry(st, transport=live_transport([]), sleep=lambda _s: None)
    eng = make_engine(f"sqlite:///{(tmp_path / 'live.db').as_posix()}")
    Base.metadata.create_all(eng)
    return MarketLensService(st, make_session_factory(eng), registry=reg, llm=UnavailableLLM(), now_fn=lambda: NOW)


def _join() -> None:
    for t in threading.enumerate():
        if t.name == "marketlens-sync":
            t.join(timeout=120)
            assert not t.is_alive()


def test_the_screen_can_prepare_the_data_without_the_cli(tmp_path):
    svc = _live(tmp_path)
    before = svc.readiness()
    assert before["recommendation_readiness"] == "NOT READY"
    assert any("데이터 준비 시작" in x for x in before["scanner_reasons"])  # the reason names the button the screen shows
    r = svc.start_sync()
    assert r["started"] is True
    _join()
    job = svc.sync_status()["job"]
    assert job["status"] == "DONE" and job["round"] >= 1 and job["finished_at"], job
    after = svc.readiness()
    assert after["sync"]["status"] == "SYNC_COMPLETE"
    assert not any("유니버스" in x for x in after["scanner_reasons"])  # the universe is in the store now
    assert svc._sync_lock.acquire(blocking=False)  # the job released its lock
    svc._sync_lock.release()


def test_a_second_start_while_one_runs_does_not_start_another(tmp_path):
    svc = _live(tmp_path)
    assert svc._sync_lock.acquire(blocking=False)  # a job is running
    try:
        r = svc.start_sync()
        assert r["started"] is False and "이미" in r["reason"]
    finally:
        svc._sync_lock.release()


def test_a_failing_round_ends_as_failed_and_frees_the_job(tmp_path, monkeypatch):
    svc = _live(tmp_path)

    def boom(**_k):  # noqa: ANN003, ANN202
        raise RuntimeError("disk full")

    monkeypatch.setattr(svc, "sync_market", boom)
    assert svc.start_sync()["started"] is True
    _join()
    job = svc.sync_status()["job"]
    assert job["status"] == "FAILED" and "disk full" in job["errors"][0]
    assert svc.start_sync()["started"] is True  # it can be started again
    _join()


def test_a_round_without_progress_stops_instead_of_repeating_the_same_calls(tmp_path, monkeypatch):
    svc = _live(tmp_path)
    calls = {"n": 0}

    def stuck(**_k):  # noqa: ANN003, ANN202
        calls["n"] += 1
        return {"status": "SYNC_PARTIAL", "bar_days_remaining": 100, "bar_days_loaded": 0, "bar_days_empty": 0, "fundamentals_ingested": 0,
                "profiles_updated": 0, "fundamentals_pending": 0, "errors": ["bars 2026-06-01: 429 rate limited"]}

    monkeypatch.setattr(svc, "sync_market", stuck)
    svc.start_sync()
    _join()
    job = svc.sync_status()["job"]
    assert calls["n"] == 1 and job["status"] == "FAILED" and "429" in job["errors"][0]


def test_a_job_left_running_by_a_closed_app_reads_as_interrupted(tmp_path):
    svc = _live(tmp_path)
    svc._sync_state({"status": "RUNNING", "round": 3})
    assert svc.sync_status()["job"]["status"] == "INTERRUPTED"


@pytest.mark.parametrize("missing,words", [("polygon_api_key", "POLYGON_API_KEY"), ("sec_user_agent", "SEC_USER_AGENT")])
def test_a_missing_key_is_named_instead_of_asking_for_a_sync_that_cannot_help(tmp_path, missing, words):
    svc = _live(tmp_path, **{missing: ""})
    reasons = svc.readiness()["scanner_reasons"]
    assert any(words in x and "설정 화면" in x for x in reasons), reasons


def test_mock_mode_needs_no_preparation_and_the_api_is_guarded():
    from fastapi.testclient import TestClient

    from marketlens.api.app import create_app
    from tests.integration.test_service_api import make_service

    svc = make_service(universe=40)
    assert svc.start_sync()["started"] is False and svc.sync_status() == {"job": None}
    app = create_app(svc.settings, service=svc, run_migrations=False)
    with TestClient(app, headers={"X-MarketLens-Client": "test"}) as c:
        assert c.post("/api/sync/start").json()["started"] is False
        assert c.get("/api/sync/status").json() == {"job": None}
    with TestClient(app) as c:  # without the client header the CSRF guard refuses the write
        assert c.post("/api/sync/start").status_code == 403
