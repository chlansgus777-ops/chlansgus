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


# ---------------------------------------------------------------- owner report 2026-09-27: "완료" in 10 s, still NOT READY
@pytest.mark.parametrize("missing,word", [("polygon_api_key", "POLYGON"), ("sec_user_agent", "SEC")])
def test_a_preparation_that_cannot_fetch_a_required_dataset_says_setup_is_needed(tmp_path, missing, word):
    """A step whose provider has no key was skipped silently and the job ended DONE ('끝남') after one round while
    the readiness stayed NOT READY. Without a required key the job ends NEEDS_SETUP and names what to set."""
    svc = _live(tmp_path, **{missing: None})
    svc.start_sync(max_rounds=3)
    _join()
    job = svc.sync_status()["job"]
    assert job["status"] == "NEEDS_SETUP", job
    assert any(word in m for m in job["missing"]), job
    last = svc.store.get_setting("last_sync") or ""
    assert "SYNC_COMPLETE" not in last  # never "complete" while a required dataset cannot be fetched


def test_with_every_key_the_preparation_does_not_ask_for_setup(tmp_path):
    svc = _live(tmp_path)
    svc.start_sync(max_rounds=1)
    _join()
    job = svc.sync_status()["job"]
    assert job["status"] != "NEEDS_SETUP" and not job.get("missing")


# ---------------------------------------------------------------- owner report 2026-09-27 (2): still 10 s, still NOT READY
def test_a_missing_quote_key_is_named_although_the_data_itself_is_complete(tmp_path):
    """Without FINNHUB_API_KEY the recommendation readiness is NOT READY (the current price cannot be checked) even
    with every stored dataset complete; the button cannot fix that. The job must say so, never 'done'."""
    svc = _live(tmp_path, finnhub_api_key=None)
    svc.start_sync()
    _join()
    job = svc.sync_status()["job"]
    assert svc.readiness()["recommendation_readiness"] == "NOT READY"
    assert job["status"] == "NEEDS_SETUP", job
    assert any("FINNHUB_API_KEY" in m for m in job["missing"]), job
    assert any("FINNHUB_API_KEY" in m for m in svc.readiness()["readiness_reasons"])  # the header's reason names it too


def test_a_job_with_nothing_left_to_fetch_is_not_done_while_the_readiness_is_not_ready(tmp_path, monkeypatch):
    """Whatever made the round fetch nothing (days recorded as empty by an older version, companies waiting for a
    retry): the job ends INCOMPLETE with the readiness reasons, not DONE."""
    svc = _live(tmp_path)

    def nothing(**_k):  # noqa: ANN003, ANN202
        return {"status": "SYNC_COMPLETE", "bar_days_remaining": 0, "bar_days_loaded": 0, "bar_days_empty": 0, "fundamentals_ingested": 0,
                "profiles_updated": 0, "fundamentals_pending": 0, "errors": [], "missing": []}

    monkeypatch.setattr(svc, "sync_market", nothing)
    svc.start_sync()
    _join()
    job = svc.sync_status()["job"]
    assert svc.readiness()["recommendation_readiness"] == "NOT READY"
    assert job["status"] == "INCOMPLETE", job
    assert job["reasons"] and any("유니버스" in r for r in job["reasons"]), job


def test_companies_whose_filings_all_failed_are_shown_with_the_cause_and_the_retry_time(tmp_path, monkeypatch):
    from marketlens.application.sync import _find
    from marketlens.providers.contracts import ProviderUnavailable

    svc = _live(tmp_path)
    fund = _find(svc.registry, "fundamental", "get_quarterly")

    def refused(_t):  # noqa: ANN001, ANN202
        raise ProviderUnavailable("unauthorized (403) — check API key / license / SEC User-Agent")

    monkeypatch.setattr(fund, "get_quarterly", refused)
    svc.start_sync()
    _join()
    job = svc.sync_status()["job"]
    assert job["status"] == "INCOMPLETE", job
    assert any("403" in f for f in job["failures"]), job
    assert job.get("retry_at"), job


# ---------------------------------------------------------------- owner request: show the real progress (%) while it runs
def test_the_preparation_reports_its_progress_while_it_runs(tmp_path, monkeypatch):
    from marketlens.application.sync import _find

    svc = _live(tmp_path)
    grouped = _find(svc.registry, "price", "get_grouped_daily")
    real = grouped.get_grouped_daily
    calls = {"n": 0}
    third, go = threading.Event(), threading.Event()

    def slow(d):  # noqa: ANN001, ANN202
        calls["n"] += 1
        if calls["n"] == 3:
            third.set()
            go.wait(timeout=20)
        return real(d)

    monkeypatch.setattr(grouped, "get_grouped_daily", slow)
    seen: list[tuple[str, int, int]] = []
    record = svc._on_sync_progress

    def spy(step, done, total, detail=""):  # noqa: ANN001, ANN202
        seen.append((step, done, total))
        record(step, done, total, detail)

    monkeypatch.setattr(svc, "_on_sync_progress", spy)
    svc.start_sync()
    assert third.wait(timeout=20)
    p = svc.sync_status()["job"]["progress"]  # mid-round: before the round has ended
    bars = p["steps"]["bars"]
    assert bars["total"] > 0 and bars["done"] == 2 and bars["detail"], p  # two sessions stored, the third being fetched
    assert 0 < p["percent"] < 100 and p.get("eta_seconds", 0) > 0, p
    go.set()
    _join()
    job = svc.sync_status()["job"]
    fin = job["progress"]
    assert all(s["done"] == s["total"] for s in fin["steps"].values()), fin
    assert job["status"] == "DONE" and fin["percent"] == 100, job
    b = [(d, t) for s, d, t in seen if s == "bars"]
    assert all(d1 <= d2 for (d1, _), (d2, _) in zip(b, b[1:])) and all(d <= t for d, t in b)  # never backwards, never above the total


# ---------------------------------------------------------------- owner report (3): "남은 가격 거래일 0" while no stock has history
def test_one_tickers_own_history_does_not_make_a_market_day_look_loaded(tmp_path):
    """The owner's store: the dashboard's SPY benchmark came from per-ticker Polygon aggregates, written back with the
    source 'polygon' for a year of sessions. The sync counted a session as loaded when ANY 'polygon' bar existed, so it
    fetched nothing ('남은 가격 거래일 0', 1 round) and no stock had history (price history 0%, market cap 0%)."""
    from marketlens.domain.market import Bar
    from marketlens.domain.market_calendar import last_completed_session
    from tests.live_fixtures import trading_days_back

    svc = _live(tmp_path)
    days = trading_days_back(last_completed_session(NOW), 250)
    svc.store.save_bars("SPY", [Bar(d, 600, 601, 599, 600, 1e7) for d in days], "polygon")  # exactly what DataAccess.bars writes back
    svc.start_sync()
    _join()
    job = svc.sync_status()["job"]
    r = svc.readiness()
    assert r["progress"]["price_history"] >= 0.9 and r["progress"]["market_cap"] >= 0.8, (job, r["scanner_reasons"])
    assert r["scanner_status"] == "SCANNER_READY" and job["status"] == "DONE", (job, r["scanner_reasons"])


def test_a_market_day_is_counted_only_from_the_market_wide_download(tmp_path):
    """Store rule behind the sync, the readiness day count and the backfill flag: the sessions of the market-wide
    grouped download — recorded when it stores them, or (a store from before the record) a session holding a whole
    market of rows — never a session holding only a few tickers' own histories."""
    from datetime import date

    from marketlens.domain.market import Bar
    from marketlens.infrastructure.db.models import PriceBarRow

    svc = _live(tmp_path)
    st = svc.store
    d1, d2, d3 = date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)
    st.save_bars("SPY", [Bar(d1, 1, 1, 1, 1, 1)], "polygon")  # one ticker's own history
    with st.sf() as s:  # a session loaded market-wide by an older version (no record of it)
        s.add_all([PriceBarRow(ticker=f"T{i}", day=d2, source="polygon", open=1, high=1, low=1, close=1, volume=1, retrieved_at=NOW) for i in range(1500)])
        s.commit()
    assert st.grouped_days() == {d2}
    st.save_grouped(d3, {"AAA": Bar(d3, 1, 1, 1, 1, 1)}, "polygon")  # the grouped download records its session
    assert st.grouped_days() == {d2, d3}


def test_the_backfill_flag_follows_the_market_history(tmp_path, monkeypatch):
    """'bars_backfill_complete' tells per-ticker reads the stored market history is authoritative. Set while the
    window only looked loaded, it is cleared once older sessions are found missing; a failure on a new recent session
    keeps it."""
    from datetime import timedelta

    from marketlens.application.sync import MarketSync, _find
    from marketlens.domain.market import Bar
    from marketlens.domain.market_calendar import last_completed_session
    from marketlens.providers.contracts import ProviderUnavailable
    from tests.live_fixtures import trading_days_back

    svc = _live(tmp_path)
    days = trading_days_back(last_completed_session(NOW), 250)
    svc.store.save_bars("SPY", [Bar(d, 600, 601, 599, 600, 1e7) for d in days], "polygon")
    svc.store.set_setting("bars_backfill_complete", "2025-11-29")  # what the old count had recorded
    svc.sync_market(max_bar_calls=5)
    assert not svc.store.get_setting("bars_backfill_complete")
    svc.start_sync()
    _join()
    flag = svc.store.get_setting("bars_backfill_complete")
    assert flag
    grouped = _find(svc.registry, "price", "get_grouped_daily")

    def down(_d):  # noqa: ANN001, ANN202
        raise ProviderUnavailable("server error 503")

    monkeypatch.setattr(grouped, "get_grouped_daily", down)
    MarketSync(svc.registry, svc.store).run(NOW + timedelta(days=3))  # Monday: Friday's session is new and fails
    assert svc.store.get_setting("bars_backfill_complete") == flag
