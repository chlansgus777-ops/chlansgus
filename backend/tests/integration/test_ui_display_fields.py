"""Display-only fields the redesigned first screen reads (UI overhaul, 2026-09-28).

They are read from what the backend already has — the exchange calendar and the analysis stored with each
recommendation — so the screen never guesses a session from the clock or writes its own reason for a candidate."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from marketlens.api.app import create_app
from marketlens.api.routes import _card_facts
from marketlens.infrastructure.db import repository as repo
from tests.integration.test_service_api import make_service


def _client(svc):
    return TestClient(create_app(svc.settings, service=svc, run_migrations=False), headers={"X-MarketLens-Client": "test"})


def test_system_reports_the_session_from_the_exchange_calendar():
    svc = make_service(universe=40)
    with _client(svc) as c:
        m = c.get("/api/system").json()["market"]
        assert m["session"] == "REGULAR"  # conftest NOW: Friday 11:00 ET
        assert m["ny_time"].startswith("2026-09-25T11:00") and m["last_completed_session"] == "2026-09-24"
        svc._clock["t"] = datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc)  # Thanksgiving 11:00 ET: a weekday, but closed
        assert c.get("/api/system").json()["market"]["session"] == "CLOSED"
        svc._clock["t"] = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)  # 08:00 ET
        assert c.get("/api/system").json()["market"]["session"] == "PREMARKET"


def test_dashboard_cards_carry_a_stored_reason_and_risk():
    svc = make_service(universe=80)
    svc.run_scan(run_committee=False)
    with _client(svc) as c:
        top = c.get("/api/dashboard").json()["top_opportunities"]
    assert top
    with svc.sf() as s:
        for r in top:
            assert "key_reason" in r and "key_risk" in r
            res = repo.get_recommendation(s, r["id"]).result
            positives = [x["text"] for comp in res["scorecard"]["components"] for x in comp["reasons"] if x["sign"] > 0]
            assert r["key_reason"] is None or r["key_reason"] in positives  # one of the stored sentences, never new text


def test_card_facts_order_veto_then_event_then_negative():
    comp = lambda name, w, sub, reasons: {"name": name, "weight": w, "subscore": sub, "available": True, "reasons": [{"text": t, "sign": g} for t, g in reasons]}  # noqa: E731
    res = {"scorecard": {"components": [comp("entry_rr", 30, 1.0, [("손익비 3", 1)]), comp("valuation", 10, 0.2, [("싸다", 1), ("비싼 편", -1)]),
                                        comp("fundamental", 20, 0.9, [("마진 우수", 1), ("부채 많음", -1)])]},
           "decision": {"vetoes": []}, "event_risk": {"level": "LOW"}}
    assert _card_facts(res) == {"key_reason": "마진 우수", "key_risk": {"kind": "negative", "code": None, "text": "부채 많음"}}
    res["event_risk"] = {"level": "HIGH", "reasons": ["실적 발표 2일 전"]}
    assert _card_facts(res)["key_risk"] == {"kind": "event", "code": "HIGH", "text": "실적 발표 2일 전"}
    res["decision"] = {"vetoes": ["STALE_PRICE"]}
    assert _card_facts(res)["key_risk"]["code"] == "STALE_PRICE"
    assert _card_facts({}) == {"key_reason": None, "key_risk": None}


def test_every_watched_stock_is_listed_on_the_home_screen():
    # screen audit 2026-09-29: a watched stock with nothing alarming (no buy signal, current, no veto) was left out,
    # so the home rail said "관심 종목이 없습니다" while the stock was on the watchlist
    svc = make_service(universe=80)
    svc.run_scan(run_committee=False)
    with svc.sf() as s:
        tickers = [r.ticker for r in repo.recommendations_for_scan(s, repo.latest_scan(s).id)]
    with _client(svc) as c:
        for t in tickers[:6]:
            c.post(f"/api/watchlist/{t}")
        listed = {a["ticker"] for a in c.get("/api/dashboard").json()["watchlist_alerts"]}
    assert set(tickers[:6]) <= listed


def test_cash_nobody_entered_is_marked_as_the_sizing_assumption():
    # real-usage audit 2026-09-29: a fresh install showed "현금 $100,000" and a $100,000 total — the sizing default,
    # presented as the user's money
    svc = make_service(universe=40)
    with _client(svc) as c:
        assert c.get("/api/portfolio").json()["cash_entered"] is False
        assert c.get("/api/dashboard").json()["portfolio"]["cash_entered"] is False
        c.put("/api/portfolio", json={"cash": 25000, "holdings": []})
        assert c.get("/api/portfolio").json()["cash_entered"] is True
        assert c.get("/api/dashboard").json()["portfolio"]["cash_entered"] is True


def test_candidate_rows_carry_the_session_of_the_scan_time():
    # real-usage audit 2026-09-29: a 06:50 ET (pre-market) scan was explained as "after-hours" — the row's session
    # was the QUOTE's (yesterday's close), not the time of the scan
    from marketlens.domain.market_calendar import classify_session

    svc = make_service(universe=40)
    svc._clock["t"] = datetime(2026, 9, 25, 10, 50, tzinfo=timezone.utc)  # 06:50 ET
    svc.run_scan(run_committee=False)
    with _client(svc) as c:
        rows = c.get("/api/opportunities").json()["rows"]
    assert rows and all(r["scan_session"] == classify_session(datetime.fromisoformat(r["as_of"])).value for r in rows)
    assert rows[0]["scan_session"] == "PREMARKET"


def test_data_gaps_are_not_listed_as_the_biggest_risk():
    # real-usage audit 2026-09-29: "가장 큰 위험: NVDA — 현재가 오래됨" — a data state is not a risk of the company
    import marketlens.api.routes as R

    svc = make_service(universe=80)
    svc.run_scan(run_committee=False)
    real = R._scan_rows

    def with_vetoes(s, ss):  # the MOCK world never has a stale quote: give three rows the vetoes a LIVE pre-market scan has
        scan, rows, results = real(s, ss)
        rows[0]["vetoes"] = ["STALE_PRICE"]
        rows[1]["vetoes"] = ["MISSING_CORE_DATA", "INSUFFICIENT_MODEL_COVERAGE"]
        rows[2]["vetoes"] = ["STALE_PRICE", "UNACCEPTABLE_LIQUIDITY"]
        return scan, rows, results

    R._scan_rows = with_vetoes
    try:
        with _client(svc) as c:
            risks = c.get("/api/dashboard").json()["major_risks"]
            ticker2 = c.get("/api/opportunities").json()["rows"][2]["ticker"]
    finally:
        R._scan_rows = real
    for r in risks:
        assert not set(r["text"].split(", ")) & R.DATA_STATE_VETOES, r
    assert {"ticker": ticker2, "text": "UNACCEPTABLE_LIQUIDITY"} in risks  # a real risk still leads the list
