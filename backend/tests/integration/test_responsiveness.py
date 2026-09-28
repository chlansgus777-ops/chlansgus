"""Owner report 2026-09-28 — "메뉴에 어떤 항목을 들어가던 1분 이상", "홈 요약 30초 이상", "데이터 준비 263/263에서 10분".

1. A screen read never waits for a provider: the home screen and the market screens answer from what is stored or
   cached, one background job per key refreshes it, and the answer says what is loading and when it was fetched.
2. A failed refresh keeps the last good value (marked), is retried after a back-off, and an answer computed before a
   change never replaces the newer one.
3. "Did the stored data change?" is a version read kept by triggers — no count over millions of bars.
4. The evaluation screen does not load the stored analysis JSON of every recommendation.
5. Every step of the data preparation reports itself before it runs.
6. An entered holding line that the trade records override is listed so the screen can offer to remove it.
"""

from __future__ import annotations

import threading
import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from marketlens.api.app import create_app
from marketlens.application.refresher import Refresher
from marketlens.domain.market import Bar
from marketlens.infrastructure.db import repository as repo
from tests.integration.test_service_api import NOW, make_service

H = {"X-MarketLens-Client": "test"}


def _client(svc):  # noqa: ANN001, ANN202
    return TestClient(create_app(svc.settings, service=svc, run_migrations=False), headers=H)


# ---------------------------------------------------------------- refresher
def test_one_background_job_per_key_and_the_last_good_value_survives_a_failure():
    r = Refresher(workers=2)
    gate, calls = threading.Event(), [0]

    def slow():  # noqa: ANN202
        calls[0] += 1
        gate.wait(3)
        return calls[0]

    snaps = [r.get("k", slow, max_age=60) for _ in range(5)]
    assert calls[0] == 1 and all(s.refreshing and not s.ready for s in snaps)  # five screens, one request
    gate.set()
    s = r.wait("k", 3)
    assert s.value == 1 and s.computed_at == s.computed_at and s.computed_at is not None and not s.refreshing

    def down():  # noqa: ANN202
        raise RuntimeError("provider down")

    r.get("k", down, max_age=0)
    s = r.wait("k", 3)
    assert s.value == 1 and s.error and "provider down" in s.error  # the previous good value, marked
    runs = r.runs
    r.get("k", down, max_age=0, retry_after=60)
    assert r.runs == runs  # retried after the back-off, not on every screen visit
    r.shutdown()


def test_max_age_zero_always_refreshes_even_when_the_clock_has_not_ticked():
    r = Refresher(workers=1, clock=lambda: 100.0)  # Windows' monotonic clock moves in ~15 ms steps (CI run 36486391132)
    r.get("k", lambda: "first", max_age=0)
    assert r.wait("k", 2).value == "first"
    r.get("k", lambda: "second", max_age=0)
    assert r.wait("k", 2).value == "second"
    r.shutdown()


def test_only_the_request_that_starts_a_job_waits_for_it():
    r = Refresher(workers=2)
    gate = threading.Event()
    t = time.monotonic()
    r.get("slow", lambda: gate.wait(5) and "done", max_age=60, wait=0.3)  # the first visit waits (bounded)
    assert 0.25 < time.monotonic() - t < 2.0
    t = time.monotonic()
    s = r.get("slow", lambda: "never", max_age=60, wait=0.3)  # another screen while it still runs: at once
    assert time.monotonic() - t < 0.25 and s.refreshing and not s.ready
    gate.set()
    assert r.wait("slow", 2).value == "done"
    r.shutdown()


def test_an_answer_computed_before_a_change_never_replaces_the_newer_one():
    r = Refresher(workers=2)
    gate = threading.Event()

    def old():  # noqa: ANN202
        gate.wait(3)
        return "before the trade"

    r.get("p", old, max_age=60)
    r.invalidate("p")  # a trade was recorded while the old read ran
    r.get("p", lambda: "after the trade", max_age=60)
    assert r.wait("p", 3).value == "after the trade"
    gate.set()
    time.sleep(0.2)
    assert r.peek("p").value == "after the trade"
    r.shutdown()


# ---------------------------------------------------------------- screens never wait for providers
def test_the_home_screen_answers_at_once_while_macro_and_calendar_are_slow(monkeypatch):
    svc = make_service(universe=40)
    svc.run_scan(run_committee=False)
    real_macro, real_events = svc.data.macro_snapshot, svc.data.events

    def slow_macro(as_of):  # noqa: ANN001, ANN202
        time.sleep(5)
        return real_macro(as_of)

    def slow_events(a, b):  # noqa: ANN001, ANN202
        time.sleep(5)
        return real_events(a, b)

    monkeypatch.setattr(svc.data, "macro_snapshot", slow_macro)
    monkeypatch.setattr(svc.data, "events", slow_events)
    with _client(svc) as c:
        t = time.monotonic()
        d = c.get("/api/dashboard").json()
        took = time.monotonic() - t
        assert took < 3.0, took  # the old path fetched FRED, then the calendar, in the request
        assert d["top_opportunities"] and d["regime_status"]["pending"] is True and d["catalysts_status"]["pending"] is True
        assert d["regime"]["primary"] == "Unknown"  # nothing made up while loading
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            d = c.get("/api/dashboard").json()
            if not d["regime_status"]["pending"] and not d["catalysts_status"]["pending"]:
                break
            time.sleep(0.2)
        assert d["regime_status"]["available"] is True and d["regime_status"]["fetched_at"]
        assert d["catalysts_status"]["available"] is True


def test_a_failing_provider_is_said_and_never_freezes_the_screen(monkeypatch):
    svc = make_service(universe=40)

    def down(as_of):  # noqa: ANN001, ANN202
        return None, "FRED 응답 없음(시험)"

    monkeypatch.setattr(svc.data, "macro_snapshot", down)
    with _client(svc) as c:
        m = c.get("/api/macro").json()
        deadline = time.monotonic() + 5
        while m.get("pending") and time.monotonic() < deadline:
            time.sleep(0.1)
            m = c.get("/api/macro").json()
        assert m["available"] is False and m["pending"] is False and "FRED" in (m["reason"] or "")
        assert c.get("/api/dashboard").status_code == 200


def test_the_issues_screen_shows_the_last_context_while_a_newer_one_is_built(monkeypatch):
    svc = make_service(universe=40)
    svc.run_scan(run_committee=False)
    old = svc.last_scan_context
    svc._clock["t"] = NOW + timedelta(hours=2)  # older than CONTEXT_MAX_AGE
    gate = threading.Event()
    real = svc.market_context

    def slow():  # noqa: ANN202
        gate.wait(10)
        return real()

    monkeypatch.setattr(svc, "market_context", slow)
    with _client(svc) as c:
        t = time.monotonic()
        body = c.get("/api/issues").json()
        assert time.monotonic() - t < 4.0  # the rebuild is held for 10 s
        assert body["refreshing"] is True and body["as_of"] == old.as_of.isoformat()  # its own time, not "now"
        gate.set()


def test_the_stock_page_does_not_wait_for_a_slow_quote(monkeypatch):
    svc = make_service(universe=40)
    svc.run_scan(run_committee=False)
    with svc.sf() as s:
        t = repo.recommendations_for_scan(s, repo.latest_scan(s).id)[0].ticker
    svc.data.cache._d.clear()

    gate = threading.Event()

    def slow_quote(ticker):  # noqa: ANN001, ANN202
        gate.wait(8)
        return svc.data.peek_quote(ticker)

    monkeypatch.setattr(svc.data, "quote", slow_quote)
    with _client(svc) as c:
        start = time.monotonic()
        r = c.get(f"/api/stocks/{t}")
        assert r.status_code == 200 and time.monotonic() - start < 5.0
        assert r.json()["recommendation"]["quote_pending"] is True
    gate.set()
    svc.refresher.wait(f"quote:{t}", 3)  # the background fetch ends inside the test


# ---------------------------------------------------------------- data version
def test_the_change_marker_is_a_version_read_that_follows_every_write(tmp_path):
    from tests.integration.test_background_sync import _live

    svc = _live(tmp_path)
    st = svc.store
    first = st.data_fingerprint()
    assert getattr(st, "_dv_ok", None) is True  # the triggers exist: no count over the bars
    d = date(2026, 9, 24)
    st.save_bars("AAA", [Bar(d, 1.0, 2.0, 0.5, 1.5, 1000.0)], "test")
    second = st.data_fingerprint()
    assert second != first
    from sqlalchemy import update

    from marketlens.infrastructure.db.models import PriceBarRow

    with st.sf() as s:  # an update in place (a split re-basing does this)
        s.execute(update(PriceBarRow).where(PriceBarRow.ticker == "AAA").values(close=1.7))
        s.commit()
    third = st.data_fingerprint()
    assert third != second
    st.save_bars("AAA", [Bar(d, 1.0, 2.0, 0.5, 1.9, 1000.0)], "test")  # an existing day is never rewritten: no change
    assert st.data_fingerprint() == third


def test_the_migration_keeps_every_row_and_installs_the_counters(tmp_path):
    import sqlite3

    from alembic import command
    from alembic.config import Config

    from marketlens.config import ALEMBIC_DIR

    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0005")
    con = sqlite3.connect(tmp_path / "m.db")
    con.execute("INSERT INTO price_bars (ticker, day, source, open, high, low, close, volume, retrieved_at) VALUES ('ZZZ', '2026-09-24', 't', 1, 1, 1, 1, 1, '2026-09-25 00:00:00.000000')")
    con.commit()
    command.upgrade(cfg, "head")
    assert con.execute("SELECT count(*) FROM price_bars").fetchone()[0] == 1
    before = dict(con.execute("SELECT name, version FROM data_versions").fetchall())
    con.execute("UPDATE price_bars SET close = 2 WHERE ticker = 'ZZZ'")
    con.commit()
    after = dict(con.execute("SELECT name, version FROM data_versions").fetchall())
    assert after["price_bars"] == before["price_bars"] + 1
    command.downgrade(cfg, "0005")  # reversible
    assert con.execute("SELECT count(*) FROM price_bars").fetchone()[0] == 1
    con.close()


# ---------------------------------------------------------------- evaluation screen
def test_the_performance_screen_reads_the_same_facts_without_the_analysis_json():
    svc = make_service(universe=40)
    svc.run_scan(run_committee=False)
    with svc.sf() as s:
        full = [(r.id, r.ticker, r.as_of, r.final_action, r.confidence) for r in repo.all_recommendations(s, mode="MOCK", until=NOW + timedelta(days=1))]
        light = [tuple(r) for r in repo.recommendation_keys(s, mode="MOCK", until=NOW + timedelta(days=1))]
    assert full and light == full


# ---------------------------------------------------------------- data preparation progress
def test_every_preparation_step_reports_itself_before_it_runs(tmp_path):
    from marketlens.application.sync import MarketSync
    from tests import live_fixtures as LF
    from tests.integration.test_background_sync import _live

    svc = _live(tmp_path)
    seen: list[tuple[str, int, int]] = []
    MarketSync(svc.registry, svc.store).run(LF.NOW, progress=lambda step, done, total, detail="": seen.append((step, done, total)))
    steps = [s for s, _d, _t in seen]
    for step in ("bars", "universe", "shares", "profiles"):
        assert step in steps, (step, steps)
    # a step is announced (0 done) before its work, so the screen never shows the previous step's count meanwhile
    assert steps.index("universe") > steps.index("bars") and seen[steps.index("universe")][1] == 0
    assert seen[steps.index("shares")][1] == 0


def test_the_time_left_is_unknown_while_a_step_runs_far_past_its_estimate():
    svc = make_service(universe=10)
    svc._on_sync_progress("bars", 263, 263, "")
    svc._on_sync_progress("universe", 0, 1, "SEC 종목 목록")
    p = svc._progress_summary()
    assert p["current"] == "universe" and p["eta_seconds"] is not None
    svc._sync_progress["_since"] -= 600  # ten minutes on a step estimated at seconds
    assert svc._progress_summary()["eta_seconds"] is None  # never "under a minute" then


# ---------------------------------------------------------------- portfolio
def test_an_entered_line_the_records_override_is_listed_for_removal():
    svc = make_service(universe=40)
    with _client(svc) as c:
        c.put("/api/portfolio", json={"holdings": [{"ticker": "AMD", "quantity": 7, "cost_basis": 50}]})
        c.post("/api/transactions", json={"ticker": "AMD", "day": (NOW.date() - timedelta(days=9)).isoformat(), "kind": "BUY", "quantity": 2, "price": 10})
        pf = c.get("/api/portfolio").json()
        assert pf["unused_manual"] == [{"ticker": "AMD", "quantity": 7}]
        c.put("/api/portfolio", json={"holdings": [{"ticker": "AMD", "quantity": 0, "cost_basis": 0}]})  # the screen's "지우기"
        pf = c.get("/api/portfolio").json()
        assert pf["unused_manual"] == [] and not any("수동 입력 줄" in n for n in pf["notes"])
        amd = next(h for h in pf["holdings"] if h["ticker"] == "AMD")
        assert amd["source"] == "ledger" and amd["quantity"] == pytest.approx(2)  # the trade records are untouched
