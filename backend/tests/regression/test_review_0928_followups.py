"""Follow-ups to the independent review of 2026-09-28 (F01–F10) beyond the reviewer's own counterexamples
(tests/acceptance_a_grade/test_independent_review_0928.py): the same meaning from calculation to storage, API, screen
and paper trading."""
from __future__ import annotations

from datetime import timedelta

import pytest

from tests.integration.test_service_api import make_service

PLAN_KEYS = {"ideal_entry": "ideal_entry", "max_buy": "max_buy", "stop": "stop", "target": "target1", "target2": "target2",
             "buy_zone_low": "acceptable_low", "buy_zone_high": "acceptable_high", "add_zone_low": "add_zone_low", "add_zone_high": "add_zone_high"}


# ---------------------------------------------------------------- F03: one price basis in the API
@pytest.mark.parametrize("ratio", [(1, 10), (10, 1)])  # a 10-for-1 split and a 1-for-10 reverse split
def test_the_stock_api_sends_the_whole_plan_and_the_chart_on_todays_share_basis(monkeypatch, ratio):
    from fastapi.testclient import TestClient

    from marketlens.api.app import create_app
    from marketlens.domain.corporate_actions import SplitEvent
    from marketlens.infrastructure.db import repository as repo

    svc = make_service(universe=80)
    svc.run_scan(run_committee=False)
    with svc.sf() as s:
        row = next(r for r in repo.all_recommendations(s) if (r.result.get("entry") or {}).get("stop"))
    t, entry, bars = row.ticker, row.result["entry"], (row.inputs or {}).get("bars") or []
    svc._clock["t"] += timedelta(days=3)  # type: ignore[attr-defined]
    split = SplitEvent(t, (svc.now() - timedelta(days=1)).date(), ratio[0], ratio[1], "polygon")
    monkeypatch.setattr(svc.data, "splits", lambda x: [split] if x == t else [])
    f = ratio[1] / ratio[0]
    app = create_app(svc.settings, service=svc, run_migrations=False)
    with TestClient(app, headers={"X-MarketLens-Client": "test"}) as c:
        body = c.get(f"/api/stocks/{t}").json()
    rec = body["recommendation"]
    assert rec["split_factor_since"] == pytest.approx(f)
    for k, snap in PLAN_KEYS.items():
        assert rec[k] == pytest.approx(entry[snap] / f), k
    assert body["analysis"]["entry"]["max_buy"] == entry["max_buy"]  # the snapshot itself stays the analysis-day record
    closes = [p["close"] for p in body["price_history"]]
    assert closes == pytest.approx([b["close"] / f for b in bars[-130:]])  # the chart under the plan lines, same basis


# ---------------------------------------------------------------- F02: a holding's sector after a restart
def test_a_holding_keeps_its_sector_before_any_market_context():
    from marketlens.infrastructure.db import repository as repo

    svc = make_service(universe=10)
    with svc.sf() as s:
        repo.upsert_holding(s, "NVDA", 10, 100)
        s.commit()
        assert svc.last_scan_context is None  # a fresh start
        h = next(h for h in svc.portfolio(s).holdings if h.ticker == "NVDA")
    assert h.sector not in ("Unknown", "") and h.sector == svc.security_master(svc.now().date())["NVDA"].sector


def test_a_holding_of_unknown_sector_counts_against_the_candidates_sector_limit():
    from marketlens.domain.enums import SizeClass
    from marketlens.domain.portfolio import CandidateProfile, Holding, Portfolio, review_candidate

    pf = Portfolio((Holding("MYST", 100, 100, "Unknown"),), 10_000)  # 50% of the account, sector not known
    r = review_candidate(pf, {"MYST": 100}, CandidateProfile("NEW", "Technology", (), 0), {})
    assert r.size_cap == SizeClass.WATCH and any("업종 미확인" in w for w in r.warnings)
    small = Portfolio((Holding("MYST", 5, 100, "Unknown"),), 10_000)  # 5%: still inside the 30% limit if it were Technology
    r2 = review_candidate(small, {"MYST": 100}, CandidateProfile("NEW", "Technology", (), 0), {})
    assert r2.size_cap == SizeClass.FULL and any("업종 미확인" in w for w in r2.warnings)


# ---------------------------------------------------------------- F06: the shared market context stays current
def test_an_older_context_never_replaces_a_newer_one_and_company_news_is_carried():
    from types import SimpleNamespace

    from marketlens.application.issue_engine import IssueBuildResult

    svc = make_service(universe=10)
    now = svc.now()
    issue = SimpleNamespace(issue_id="co-1", publish_time=now - timedelta(days=1), importance=0.9)
    old_issue = SimpleNamespace(issue_id="co-old", publish_time=now - timedelta(days=9), importance=0.9)
    company = IssueBuildResult([issue, old_issue], {}, 0)
    scan_ctx = SimpleNamespace(as_of=now - timedelta(hours=2), issues=company, company_issues=company, securities={})
    svc._adopt_context(scan_ctx)
    fresh = SimpleNamespace(as_of=now, issues=IssueBuildResult([], {}, 0), company_issues=None, securities={})
    svc._adopt_context(fresh)
    assert svc.last_scan_context is fresh
    assert [i.issue_id for i in fresh.issues.issues] == ["co-1"]  # carried inside the news window, the 9-day-old one not
    svc._adopt_context(SimpleNamespace(as_of=now - timedelta(days=1), issues=None, company_issues=None, securities={}))
    assert svc.last_scan_context is fresh  # an older one (a slow scan finishing late) is ignored


def test_the_issues_screen_rebuilds_a_context_older_than_the_limit():
    from types import SimpleNamespace

    from marketlens.application.services import CONTEXT_MAX_AGE

    svc = make_service(universe=10)
    stale = SimpleNamespace(as_of=svc.now() - CONTEXT_MAX_AGE - timedelta(minutes=1), issues=None, company_issues=None, securities={})
    svc.last_scan_context = stale
    ctx = svc.market_context()
    assert ctx is not stale and ctx.as_of == svc.now()
    assert svc.market_context() is ctx  # young enough: reused, not rebuilt on every request


# ---------------------------------------------------------------- F07: scan and data preparation exclude each other
def _live_store(svc):
    svc.store = object()  # start_sync / sync_market only run with a LIVE store; the work itself is replaced below
    svc.sync_status = lambda: {}
    return svc


def test_a_direct_sync_is_refused_while_a_scan_runs_and_a_scan_while_a_sync_runs():
    from marketlens.application.services import ScanRefused

    svc = _live_store(make_service(universe=10))
    svc._lock.acquire()  # a scan is running
    try:
        assert svc.sync_market()["status"] == "REFUSED"
    finally:
        svc._lock.release()
    svc._sync_run.acquire()  # a sync from the CLI / scheduler / POST /api/sync is running
    try:
        with pytest.raises(ScanRefused):
            svc.run_scan(run_committee=False)
    finally:
        svc._sync_run.release()


def test_racing_starts_never_run_a_scan_and_a_sync_together():
    import threading

    from marketlens.application.services import ScanRefused

    for _ in range(20):
        svc = _live_store(make_service(universe=10))
        inside = {"scan": 0, "sync": 0, "both": 0}
        go = threading.Barrier(2)

        def scan_body(*_a, **_k):
            inside["scan"] += 1
            inside["both"] += bool(svc._sync_lock.locked() or svc._sync_run.locked())
            return None

        def sync_rounds(*_a):
            inside["sync"] += 1
            inside["both"] += bool(svc._lock.locked())
            svc._sync_lock.release()

        svc._run_scan_locked = scan_body
        svc._sync_rounds = sync_rounds

        def scan():
            go.wait()
            try:
                svc.run_scan(run_committee=False)
            except ScanRefused:
                pass

        def sync():
            go.wait()
            svc.start_sync()

        ts = [threading.Thread(target=scan), threading.Thread(target=sync)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(5)
        assert inside["both"] == 0, inside


# ---------------------------------------------------------------- F08: the latest few never load the whole history
def test_the_latest_recommendation_loads_only_the_rows_it_returns():
    from sqlalchemy import event

    from marketlens.infrastructure.db.models import RecommendationRow

    svc = make_service(universe=10)
    _r, _c, rid = svc.analyze("NVDA")
    with svc.sf() as s:
        row = s.get(RecommendationRow, rid)
        cols = {c.name: getattr(row, c.name) for c in RecommendationRow.__table__.columns if c.name != "id"}
        for i in range(60):
            s.add(RecommendationRow(**(cols | {"as_of": row.as_of - timedelta(minutes=i + 1)})))
        s.commit()
    loaded: list[int] = []
    listen = lambda target, _ctx: loaded.append(target.id)  # noqa: E731
    event.listen(RecommendationRow, "load", listen)
    try:
        with svc.sf() as s:
            latest = svc.company_recommendations(s, "NVDA", limit=1)
            loaded_one = len(loaded)
            five = svc.company_recommendations(s, "NVDA", limit=5)
    finally:
        event.remove(RecommendationRow, "load", listen)
    assert [r.id for r in latest] == [rid] and loaded_one == 1
    assert len(five) == 5 and [r.as_of for r in five] == sorted((r.as_of for r in five), reverse=True)
    assert len(loaded) <= 1 + 5


# ---------------------------------------------------------------- F09: no key in what the app stores or returns
def test_a_configured_short_key_and_quoted_json_keys_never_reach_health():
    import httpx

    from marketlens.infrastructure.health import HealthRegistry
    from marketlens.infrastructure.logging import configure_logging, redact_text
    from marketlens.providers.live.http import HttpClient

    configure_logging("WARNING", ["SHORTK3Y"])  # a key too short for any shape rule: only the configured value finds it
    try:
        body = {"error": "key SHORTK3Y is not valid", "detail": {"apiKey": "OTHERKEY12"}}
        http = HttpClient("https://example.invalid", transport=httpx.MockTransport(lambda _r: httpx.Response(401, json=body)))
        try:
            http.get_json("/x")
        except Exception as e:  # noqa: BLE001 - the error text is what is checked
            text = str(e)
        assert "SHORTK3Y" not in text and "OTHERKEY12" not in text
        reg = HealthRegistry()
        reg.get("p", "price", "LIVE")
        reg.record("p", False, 1.0, "provider said: token='SHORTK3Y' and {\"secret\": \"s3cr3tvalue\"}")
        err = reg.get("p", "price", "LIVE").last_error or ""
        assert "SHORTK3Y" not in err and "s3cr3tvalue" not in err
        assert redact_text("input_tokens=12 and 5 tokens") == "input_tokens=12 and 5 tokens"  # counters are not secrets
    finally:
        configure_logging("WARNING", [])


# ---------------------------------------------------------------- F10: "cannot value" is never "0" or "nothing missing"
def test_the_valuation_status_separates_empty_partial_and_unavailable():
    from datetime import date

    from marketlens.domain.portfolio import Holding, Portfolio, portfolio_snapshot

    d1, d2 = date(2026, 9, 23), date(2026, 9, 24)
    empty = portfolio_snapshot(Portfolio((), 1000), {})
    assert empty.valuation_status == "EMPTY" and empty.missing_prices == ()
    both = Portfolio((Holding("A", 1, 100, "Tech"), Holding("B", 1, 100, "Tech")), 1000)
    full = portfolio_snapshot(both, {"A": {d1: 100, d2: 110}, "B": {d1: 100, d2: 90}})
    assert full.valuation_status == "COMPLETE" and full.nav == 1200
    partial = portfolio_snapshot(both, {"A": {d2: 110}})
    assert partial.valuation_status == "PARTIAL" and partial.missing_prices == ("B",)
    none = portfolio_snapshot(both, {"A": {d1: 100}, "B": {d2: 100}})
    assert none.valuation_status == "UNAVAILABLE" and set(none.missing_prices) == {"A", "B"}


def test_an_unvalued_account_gets_no_buy_amount():
    from types import SimpleNamespace

    from marketlens.api.routes import _position_plan
    from marketlens.infrastructure.db import repository as repo

    svc = make_service(universe=10)
    with svc.sf() as s:
        repo.set_setting(s, "portfolio_cash", "1000")
        repo.upsert_holding(s, "NVDA", 10, 100)
        s.commit()
        svc.data.bars = lambda *a, **k: SimpleNamespace(value=[])  # no closes at all for the holding
        row = SimpleNamespace(ticker="AAPL", final_action="BUY", result={"decision": {"size_limit": None}}, size_class=None)
        p = _position_plan(svc, s, row, {"current_status": "CURRENT", "actionable_now": True, "price": 100.0, "stop": 95.0})
    assert p["available"] is False and "평가" in p["reason"]
