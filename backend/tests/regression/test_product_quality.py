"""Offline regression cases for the September 30 product review."""
from dataclasses import replace
from datetime import timedelta

from marketlens.application.live_judge import LivePlan, judge
from marketlens.application.toss_quotes import live_quote
from marketlens.api.routes import _overlay_live, _row_summary
from marketlens.infrastructure.db import repository as repo
from tests.integration.test_service_api import NOW, make_service
from tests.integration.test_transactions import client  # noqa: F401


def test_analysis_returns_immediately_and_deduplicates(client, monkeypatch):
    import threading
    import time
    c, svc = client
    entered, release = threading.Event(), threading.Event()
    calls = []
    def slow(*args, **kwargs):
        calls.append(args)
        entered.set()
        assert release.wait(5)
        return None, None, 42
    monkeypatch.setattr(svc, "analyze", slow)
    t = time.perf_counter()
    try:
        response = c.post("/api/stocks/NVDA/analysis")
        assert response.status_code == 202 and response.json()["status"] == "RUNNING"
        assert time.perf_counter() - t < .5
        assert entered.wait(1)
        assert c.post("/api/stocks/NVDA/analysis").status_code == 202
        assert len(calls) == 1
        assert c.get("/api/stocks/NVDA").status_code == 404
    finally:
        release.set()
        svc.analyses.wait("analysis:NVDA", 5)
    assert c.get("/api/stocks/NVDA/analysis").json()["status"] == "DONE"


def test_slow_collection_does_not_block_account_or_live_workers(client):
    import threading
    c, svc = client
    release = threading.Event()
    try:
        for i in range(3):
            svc.refresher.get(f"slow:{i}", lambda: release.wait(5), max_age=1)
        svc.accounts.get("isolated", lambda: "account", max_age=1)
        svc.realtime.get("isolated", lambda: "live", max_age=1)
        assert svc.accounts.wait("isolated", .5).value == "account"
        assert svc.realtime.wait("isolated", .5).value == "live"
    finally:
        release.set()


def plan():
    return LivePlan("NVDA", 1, NOW, "BUY", True, True, 100, 102, 95, 115, 99, 2)


def test_after_hours_daily_profit_compares_to_previous_trading_close():
    from types import SimpleNamespace
    from marketlens.application.account_live import _prev_close, _close_move
    from marketlens.domain.enums import TradingSession
    from marketlens.domain.market_calendar import previous_trading_day
    wanted = previous_trading_day(NOW.date())
    calls = []
    def bars(ticker, start, end):
        calls.append((start, end))
        return [SimpleNamespace(day=wanted, close=90), SimpleNamespace(day=NOW.date(), close=100)]
    svc = SimpleNamespace(store=SimpleNamespace(bars=bars))
    q = SimpleNamespace(timestamp=NOW.replace(hour=22), session=TradingSession.AFTER_HOURS)
    assert _prev_close(svc, "NVDA", q) == 90
    assert calls[-1] == (wanted, wanted)
    close, move, at = _close_move(svc, "NVDA", NOW.date())
    assert (close, move) == (100, 10)
    assert at.date() == NOW.date()


def test_expired_plan_cannot_be_a_live_buy():
    now = NOW + timedelta(days=7)
    verdict = judge(plan(), 100, now, now)
    assert not verdict["valid_now"]


def test_stream_judgment_expires_like_the_live_board_after_sixty_seconds():
    p = replace(plan(), live_price=True)
    assert judge(p, 100, NOW, NOW)["valid_now"]
    later = judge(p, 100, NOW, NOW + timedelta(seconds=61))
    assert not later["quote_current"]
    assert not later["valid_now"]


def test_market_closed_does_not_make_any_old_trade_current():
    now = NOW + timedelta(days=1)
    verdict = judge(plan(), 100, NOW - timedelta(days=3), now)
    assert not verdict["quote_current"]
    assert not verdict["valid_now"]


def test_newly_received_old_toss_trade_is_not_live():
    svc = make_service(universe=40)
    try:
        svc.quotes.ingest_poll("NVDA", 100, NOW - timedelta(days=1), "toss")
        assert live_quote(svc.quotes, "NVDA") is None
        assert svc._fresh_quote("NVDA") is None
    finally:
        svc.stop_background()


def test_cached_live_verdict_expires_without_a_new_trade():
    svc = make_service(universe=40)
    try:
        svc.run_scan(run_committee=False)
        with svc.sf() as s:
            row = repo.recommendations_for_scan(s, repo.latest_scan(s).id)[0]
            rid, ticker, price = row.id, row.ticker, row.price or 100
        svc.quotes.ingest_poll(ticker, price, NOW, "toss")
        svc.live_rejudge()
        assert svc.rejudged(rid)
        svc._clock["t"] = NOW + timedelta(minutes=30)
        live = svc.rejudged(rid)
        assert live["current_status"] != "CURRENT"
        assert live["actionable_now"] is not True
    finally:
        svc.stop_background()


def test_cached_live_and_alert_plan_require_review_after_major_new_issue():
    from types import SimpleNamespace
    svc = make_service(universe=40)
    try:
        svc.run_scan(run_committee=False)
        with svc.sf() as s:
            row = repo.recommendations_for_scan(s, repo.latest_scan(s).id)[0]
            rid, ticker, price = row.id, row.ticker, row.price or 100
        svc.quotes.ingest_poll(ticker, price, NOW, "toss")
        svc.live_rejudge()
        svc.last_scan_context = SimpleNamespace(issues=SimpleNamespace(issues=[SimpleNamespace(
            importance=100, publish_time=NOW + timedelta(seconds=1), affected_companies=(ticker,), title="새 중요 이슈")]))
        live = svc.rejudged(rid)
        assert live["current_status"] == "NEEDS_REVALIDATION"
        assert not live["actionable_now"]
        assert "새 중요 이슈" in live["current_status_reason"]
        assert not svc._live_plan(replace(plan(), rec_id=rid, ticker=ticker)).data_ok
    finally:
        svc.stop_background()


def test_live_price_does_not_release_committee_watch():
    svc = make_service(universe=40)
    try:
        svc.run_scan(run_committee=False)
        with svc.sf() as s:
            row = repo.recommendations_for_scan(s, repo.latest_scan(s).id)[0]
            row.final_action, row.deterministic_action, row.committee_status = "WATCH", "BUY", "COMPLETED"
            s.commit()
            rid, ticker, price = row.id, row.ticker, row.price or 100
        svc.quotes.ingest_poll(ticker, price, NOW, "toss")
        svc.live_rejudge()
        assert svc.rejudged(rid)["action"] not in ("BUY", "BUY SMALL", "ADD")
        with svc.sf() as s:
            row = svc.latest_company_recommendation(s, ticker)
            summary = _row_summary(row, svc)
            _overlay_live(summary, svc.rejudged(rid))
            assert summary["actionable_now"] is not True
        svc.analyze(ticker, run_committee=False, persist=True)
        with svc.sf() as s:
            newer = svc.latest_company_recommendation(s, ticker)
            assert newer.final_action not in ("BUY", "BUY SMALL", "ADD")
            assert newer.committee_status == "REVIEW_REQUIRED"
    finally:
        svc.stop_background()


def test_account_change_invalidates_live_and_stored_buy(client):
    c, svc = client
    svc.run_scan(run_committee=False)
    rows = c.get("/api/opportunities").json()["rows"]
    buy = next(r for r in rows if r["action"] in ("BUY", "BUY SMALL"))
    svc.quotes.ingest_poll(buy["ticker"], buy["price"], NOW, "toss")
    svc.live_rejudge()
    assert c.put("/api/portfolio", json={"cash": 10}).status_code == 200
    after = c.get("/api/opportunities").json()["rows"]
    same = next(r for r in after if r["id"] == buy["id"])
    assert not same["actionable_now"]
    assert "계좌" in same["current_status_reason"]
    assert not svc.rejudged(buy["id"])["actionable_now"]


def test_size_limit_is_not_released_by_price(client):
    c, svc = client
    svc.run_scan(run_committee=False)
    with svc.sf() as s:
        row = next(r for r in repo.recommendations_for_scan(s, repo.latest_scan(s).id) if r.final_action == "BUY")
        row.size_class = "SMALL"
        row.committee_status = "COMPLETED"
        row.final_action = "BUY SMALL"
        s.commit()
        rid, ticker, price = row.id, row.ticker, row.price
    svc.quotes.ingest_poll(ticker, price, NOW, "toss")
    svc.live_rejudge()
    live = svc.rejudged(rid)
    assert live["action"] == "BUY SMALL"
    assert live["size_limit"] == "SMALL"
