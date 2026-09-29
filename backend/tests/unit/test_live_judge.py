"""Real-time upgrade (owner 2026-09-29: "1초1초마다 내가 신경안써도 되는 프로그램"): the price-dependent verdict of a stored
recommendation is re-judged on every quote, and a zone change becomes an alert — without a scan."""

from datetime import datetime, timedelta, timezone

import pytest

from marketlens.application.live_judge import (ABOVE_MAX, BUY_ZONE, HOLD_RANGE, RR_LOW, STOP_HIT, TARGET_HIT, LiveJudge, LivePlan, judge)

UTC = timezone.utc
NOW = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)  # Friday 11:00 ET, regular session


def plan(**over) -> LivePlan:
    base = dict(ticker="NVDA", rec_id=1, as_of=NOW - timedelta(minutes=30), action="BUY", bullish=True, data_ok=True, rec_price=100.0,
                max_buy=102.0, stop=92.0, target1=125.0, ideal_entry=99.0, min_rr=2.0)
    return LivePlan(**(base | over))


def test_zones_follow_the_price():
    fresh = NOW - timedelta(seconds=5)
    assert judge(plan(), 100.0, fresh, NOW)["zone"] == BUY_ZONE
    assert judge(plan(), 103.0, fresh, NOW)["zone"] == ABOVE_MAX
    assert judge(plan(), 91.5, fresh, NOW)["zone"] == STOP_HIT
    assert judge(plan(), 126.0, fresh, NOW)["zone"] == TARGET_HIT
    # inside max buy but the reward/risk left is too small: (125-101)/(101-98)=8 ok; with a tight stop it is not
    assert judge(plan(stop=99.0, target1=104.0), 101.0, fresh, NOW)["zone"] == RR_LOW
    held = plan(action="HOLD", bullish=False, held=True, held_cost=80.0, held_qty=10.0)
    j = judge(held, 110.0, fresh, NOW)
    assert j["zone"] == HOLD_RANGE and j["pnl_pct"] == pytest.approx(0.375) and j["pnl_abs"] == pytest.approx(300.0)


def test_buy_now_needs_a_current_quote_and_data():
    j = judge(plan(), 100.0, NOW - timedelta(seconds=5), NOW)
    assert j["valid_now"] and j["rr_now"] == pytest.approx(25 / 8)
    assert not judge(plan(), 100.0, NOW - timedelta(minutes=25), NOW)["valid_now"]  # a 25-minute-old price proves nothing
    assert not judge(plan(data_ok=False), 100.0, NOW - timedelta(seconds=5), NOW)["valid_now"]
    assert not judge(plan(action="WATCH", bullish=False), 100.0, NOW - timedelta(seconds=5), NOW)["valid_now"]
    # the market closed: the final close is the current price
    sat = datetime(2026, 9, 26, 15, 0, tzinfo=UTC)
    assert judge(plan(), 100.0, datetime(2026, 9, 25, 20, 0, tzinfo=UTC), sat)["valid_now"]


def test_zone_changes_become_alerts_and_ask_for_a_reanalysis():
    j = LiveJudge(now=lambda: NOW)
    asked: list[tuple[str, str]] = []
    j.on_reanalyze = lambda t, why: asked.append((t, why))
    j.set_plans([plan(held=True, held_cost=90.0, held_qty=5.0)])
    j.observe("NVDA", 103.0, NOW)  # first sight: above max buy — not an event
    assert j.alerts() == []
    j.observe("NVDA", 101.0, NOW)  # back inside the plan
    j.observe("NVDA", 101.5, NOW)  # same zone: nothing new
    j.observe("NVDA", 91.0, NOW)  # stop
    kinds = [a["kind"] for a in j.alerts()]
    assert kinds == ["BUY_ZONE", "STOP_HIT"]
    assert j.alerts()[-1]["level"] == "danger" and "매도 검토" in j.alerts()[-1]["text"]
    assert asked == [("NVDA", STOP_HIT)]
    assert j.alerts(after=j.alerts()[0]["id"])[0]["kind"] == "STOP_HIT"


def test_a_stale_price_announces_nothing_and_a_new_analysis_does_not_repeat_an_alert():
    j = LiveJudge(now=lambda: NOW)
    j.set_plans([plan(held=True, held_cost=90.0, held_qty=5.0)])
    j.observe("NVDA", 103.0, NOW - timedelta(hours=20))  # yesterday's close
    j.observe("NVDA", 100.0, NOW - timedelta(hours=20))
    assert j.alerts() == []
    j.observe("NVDA", 100.0, NOW)
    j.observe("NVDA", 91.0, NOW)  # stop: announced once
    j.set_plans([plan(rec_id=2, held=True, held_cost=90.0, held_qty=5.0)])  # the automatic re-analysis keeps the stop
    j.observe("NVDA", 90.5, NOW)  # still beyond it under the new plan: not a second alert (live run 2026-09-29)
    assert [a["kind"] for a in j.alerts()] == ["STOP_HIT"]
    j.set_plans([plan(rec_id=3, max_buy=99.0)])
    j.observe("NVDA", 100.0, NOW)  # back above the stop but over the new max buy: a real change, no buy-zone exit spam
    assert [a["kind"] for a in j.alerts()] == ["STOP_HIT"]


def test_unknown_names_and_bad_prices_are_ignored():
    j = LiveJudge(now=lambda: NOW)
    j.set_plans([plan()])
    assert j.annotate("AMD", 10.0, NOW) is None
    assert j.annotate("NVDA", None, NOW) is None and j.annotate("NVDA", 0.0, NOW) is None
    j.observe("NVDA", -1.0, NOW)
    assert j.alerts() == []


def test_the_service_judges_quote_rows_after_a_scan_and_serves_alerts():
    from types import SimpleNamespace

    from tests.integration.test_service_api import make_service
    from tests.integration.test_ui_display_fields import _client

    svc = make_service(universe=60)
    svc.run_scan(run_committee=False)
    svc._load_live_plans()
    t = svc.judge.tickers()[0]
    p = svc.judge.plan(t)
    now = svc.now()
    svc.quotes.ingest_snapshot(t, SimpleNamespace(price=p.rec_price, timestamp=now - timedelta(seconds=3), volume=None, source="test", previous_close=None))
    row = svc.quotes.rows(tickers=[t])[0]
    assert row["judge"]["rec_id"] == p.rec_id and row["judge"]["zone"] and row["judge"]["move_pct"] == pytest.approx(0.0)
    assert set(svc.quotes._pinned["candidates"]) <= set(svc.judge.tickers())
    svc.judge.add(t, "BUY_ZONE", "positive", "test")
    with _client(svc) as c:
        body = c.get("/api/alerts").json()
    assert body["last_id"] >= 1 and body["alerts"][-1]["ticker"] == t


def test_a_change_drops_the_plans_without_reading_the_store_from_the_changing_request():
    """CI 2026-09-29: a trade record, and on Windows a stored identity, were lost when every change started the plan
    reload in the background — on the shared in-memory connection its read raced the request's commit. A change now
    only drops the plans; the next quote route or stream tick reloads them."""
    from tests.integration.test_service_api import make_service

    svc = make_service(universe=40)
    svc.live_plans()
    if (job := svc.refresher._inflight.get("live-plans")) is not None:
        job.result(timeout=30)
    runs = svc.refresher.runs
    svc.analyze("NVDA", run_committee=False, persist=True)
    svc.invalidate_live_plans()
    assert svc.refresher.runs == runs and svc.refresher.peek("live-plans").value is None
    svc.live_plans()
    assert svc.refresher.runs == runs + 1
