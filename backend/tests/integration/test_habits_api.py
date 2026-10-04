"""내 매매 진단 / 내 매매 규칙 through the API, on the spec-following fake Toss server (all orders made up): the report
reads the synced executions read-only, counts per sale order, keeps the virtual sample apart, saves the owner's rules,
notes and stops, exports CSV, extends one name's history, and never puts the key or the account number in a response."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from marketlens.domain.market import Bar
from tests.integration.test_toss_broker import connect, world  # noqa: F401
from tests.toss_fake import holding, order


def _bars(prices: dict[str, dict[date, tuple[float, float]]]):  # noqa: ANN202
    """Daily bars per symbol: (low, high) on the given days, else around the last level."""
    def bars_for(sym: str, a: date, z: date) -> list[Bar] | None:
        days = prices.get(sym)
        if not days:
            return None
        out, d, last = [], a, None
        while d <= z:
            if d.weekday() < 5:
                lo, hi = days.get(d, last or next(iter(days.values())))
                last = (lo, hi) if d in days else last
                out.append(Bar(d, (lo + hi) / 2, hi, lo, (lo + hi) / 2, 1e6))
            d += timedelta(days=1)
        return out
    return bars_for


@pytest.fixture()
def account(world, monkeypatch):  # noqa: F811, ANN001, ANN201
    c, svc, fake, keys = world
    fake.orders = [order(1, "NVDA", "BUY", "10", "100.00", "2026-09-01"), order(2, "NVDA", "SELL", "10", "101.00", "2026-09-08"),
                   order(3, "AMD", "BUY", "5", "50.00", "2026-09-02"), order(4, "AMD", "SELL", "5", "40.00", "2026-09-10"),
                   order(5, "NVDA", "BUY", "10", "105.00", "2026-09-15"),
                   order(6, "NVDA", "BUY", "3", "99.00", "2026-09-16", status="CANCELED", filled="0")]  # nothing filled: not a trade
    fake.items = [holding("NVDA", "10", "105.00", "96.00")]
    nv = {date(2026, 8, 31): (99.5, 100.5), date(2026, 9, 1): (99.5, 100.5), date(2026, 9, 3): (95.8, 100.0), date(2026, 9, 4): (99.0, 100.5),
          date(2026, 9, 8): (100.5, 101.5), date(2026, 9, 15): (104.0, 106.0), date(2026, 9, 18): (95.0, 104.0), date(2026, 9, 25): (95.5, 97.0)}
    amd = {date(2026, 9, 1): (49.5, 50.5), date(2026, 9, 4): (45.0, 47.0), date(2026, 9, 9): (41.0, 43.0), date(2026, 9, 10): (39.5, 41.0)}
    import marketlens.api.habits_routes as R

    monkeypatch.setattr(R, "_bars_fn", lambda _s: _bars({"NVDA": nv, "AMD": amd}))
    assert connect(c, fake).status_code == 200
    return c, svc, fake


def test_the_report_reads_the_account_and_counts_sale_orders(account):
    c, svc, fake = account
    r = c.get("/api/habits")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["is_sample"] is False and rep["source"] == "toss" and rep["meta"]["connected"] is True
    orders = {o["symbol"]: o for o in rep["orders"]}
    assert orders["NVDA"]["pattern"] == "CANDIDATE" and orders["NVDA"]["mae"] == pytest.approx(-4.2)
    assert orders["AMD"]["pattern"] == "NOT" and orders["AMD"]["net_pnl"] == pytest.approx(-50.2)  # fees 0.10 + 0.10
    assert rep["summary"]["sell_orders"] == 2 and rep["summary"]["breakeven"] == {**rep["summary"]["breakeven"], "candidates": 1, "evaluable": 2}
    assert rep["open_lots"] == [{"symbol": "NVDA", "currency": "USD", "quantity": 10.0, "buy_order_id": "ord0005", "entry": 105.0,
                                 "bought_at": rep["open_lots"][0]["bought_at"], "basis": "체결 기록"}]
    nv = next(p for p in rep["plans"] if p["symbol"] == "NVDA")
    # average 105 − 7 % = 97.65; Toss's last valuation 96.00 is below it → the rule says sell everything
    assert nv["stop"] == pytest.approx(97.65) and nv["price"] == pytest.approx(96.0) and nv["action"] == "STOP"
    assert nv["opened"] and nv["adds_done"] == 0 and nv["take1"] == pytest.approx(115.5)
    assert any(f["id"] == "open_losers" for f in rep["diagnosis"]["findings"])
    body = r.text
    assert fake.secret not in body and "12345678901" not in body


def test_rules_notes_stops_csv_and_the_sample_stay_apart(account):
    c, svc, fake = account
    r = c.put("/api/habits/rules", json={"trade": {"stop_pct": -5, "take1_pct": 8}, "pattern": {"dip_pct": -5}})
    assert r.status_code == 200 and r.json()["trade"]["saved_at"]
    rep = c.get("/api/habits").json()
    assert rep["rules_saved"] is True and rep["trade_rules"]["stop_pct"] == -5
    assert {o["symbol"]: o["pattern"] for o in rep["orders"]}["NVDA"] == "NOT"  # −4.2 % no longer reaches −5 %
    assert c.put("/api/habits/rules", json={"trade": {"stop_pct": 3}}).status_code == 422
    tid = next(t["id"] for t in rep["trades"] if t["symbol"] == "NVDA")
    assert c.put(f"/api/habits/notes/{tid}", json={"buy_reason": "실적", "during": "버팀", "sell_reason": "본전"}).status_code == 200
    d = c.get(f"/api/habits/trades/{tid}").json()
    assert d["note"]["sell_reason"] == "본전" and d["raw"]["buy"]["order_id"] == "ord0001" and d["bars"] and d["calc"]
    s = c.put("/api/habits/stops/ord0001", json={"stop": 97.0}).json()
    assert s["stop"] == 97.0
    rep2 = c.get("/api/habits").json()
    t = next(t for t in rep2["trades"] if t["symbol"] == "NVDA")
    assert t["stop"]["status"] == "TOUCHED_HELD" and t["stop"]["pre_recorded"] is False  # entered now, after the purchase
    csv = c.get("/api/habits/export.csv")
    assert csv.status_code == 200 and "NVDA" in csv.text and "토스증권 체결 기록" in csv.text
    sample = c.get("/api/habits?sample=1").json()
    assert sample["is_sample"] is True and all(o["symbol"].startswith("SAMP") for o in sample["orders"])
    assert sample["summary"]["breakeven"]["candidates"] == 2 and sample["summary"]["breakeven"]["repeated"] is True
    assert {f["id"] for f in sample["diagnosis"]["findings"]} >= {"breakeven", "average_down"}
    assert "가상 샘플" in c.get("/api/habits/export.csv?sample=1").text
    plans = c.get("/api/habits/plans?ticker=NVDA").json()
    assert plans["plans"][0]["symbol"] == "NVDA" and plans["rules"]["stop_pct"] == -5


def test_extend_reads_one_name_without_a_start_date(account):
    c, svc, fake = account
    fake.orders.append(order(9, "AMD", "BUY", "2", "60.00", "2024-01-05"))  # outside the regular one-year window
    before = len(fake.requests)
    r = c.post("/api/habits/extend", json={"symbol": "amd"})
    assert r.status_code == 200 and r.json()["symbol"] == "AMD" and r.json()["added"] == 1
    reqs = [q for q in fake.requests[before:] if q.url.path == "/api/v1/orders"]
    assert reqs and all("from" not in q.url.params and q.url.params.get("symbol") == "AMD" for q in reqs)
    assert all(q.method == "GET" for q in fake.requests[before:] if q.url.path != "/oauth2/token")
    assert c.post("/api/habits/extend", json={"symbol": "A B"}).status_code == 422


def test_without_a_connection_the_report_is_empty_not_invented(world):  # noqa: F811
    c, *_ = world
    rep = c.get("/api/habits").json()
    assert rep["meta"]["connected"] is False and rep["orders"] == [] and rep["trades"] == [] and rep["plans"] == []
    assert rep["diagnosis"]["closed"] == 0


def test_the_pc_check_prints_raw_records_next_to_the_calculation_without_secrets(account, capsys):
    """`marketlens habits-check` (run on the owner's PC with the real account): representative trades' raw Toss records
    beside the calculation; no key, token or account number."""
    import json as _json

    from marketlens.workers.cli import _habits_check

    c, svc, fake = account
    assert _habits_check(svc, False, 3) == 0
    out = capsys.readouterr().out
    assert fake.secret not in out and "12345678901" not in out and (svc.broker.client._token or "zz-no-token") not in out
    d = _json.loads(out)
    nv = next(x for x in d["대표 거래 대조"] if x["계산"]["symbol"] == "NVDA")
    assert nv["원본 매수"]["avg_price"] == "100.00" and nv["원본 매도"]["avg_price"] == "101.00" and nv["계산"]["entry"] == 100.0
    assert d["보유 종목 지금 할 일"][0]["action_ko"] == "손절"


def test_rule_alerts_reach_the_alert_center_on_a_live_price(account):
    """A held name's live price below the saved rule's stop is announced once in the app's alert center."""
    c, svc, fake = account
    assert c.put("/api/habits/rules", json={"trade": {"stop_pct": -5}}).status_code == 200
    assert svc.rule_watch.load() == 1  # NVDA (the one holding)
    now = svc.now()
    svc._on_price("NVDA", 100.5, now)  # 105 × 0.95 = 99.75 < 100.5: hold
    svc._on_price("NVDA", 99.0, now)
    svc._on_price("NVDA", 98.0, now)
    rows = [a for a in c.get("/api/alerts").json()["alerts"] if a["kind"].startswith("RULE_")]
    assert len(rows) == 1 and rows[0]["kind"] == "RULE_STOP" and rows[0]["ticker"] == "NVDA" and "$99.75" in rows[0]["text"]
    assert fake.secret not in rows[0]["text"]


def test_without_a_broker_the_rules_apply_to_the_holdings_entered_in_the_app(world):  # noqa: F811, ANN001
    """No Toss account: a holding entered in the app (manual line or trade records) still gets its stop, take-profit
    and add level under the rules — and a live price below the stop is announced."""
    c, svc, _fake, _keys = world  # never connected
    assert c.put("/api/portfolio", json={"cash": 1000, "holdings": [{"ticker": "MK0001", "quantity": 10, "cost_basis": 200}]}).status_code == 200
    r = c.get("/api/habits/plans").json()
    assert r["connected"] is False and r["source"] == "app"
    p = next(x for x in r["plans"] if x["symbol"] == "MK0001")
    assert p["stop"] == pytest.approx(186.0) and p["take1"] == pytest.approx(220.0) and p["action"] in ("HOLD", "ADD", "TAKE1", "STOP")
    assert svc.rule_watch.load() == 1
    svc._on_price("MK0001", 185.0, svc.now())
    assert any(a["kind"] == "RULE_STOP" and a["ticker"] == "MK0001" for a in c.get("/api/alerts").json()["alerts"])
