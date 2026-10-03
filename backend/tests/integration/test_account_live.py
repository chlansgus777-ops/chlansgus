"""Owner 2026-09-29: "토스랑 연동한 보유종목이랑 원화기준 수익이 안 맞아, 오늘의 브리핑의 내 계좌 수익도 안 맞고".
The account view (application/account_live.py, /api/portfolio/live) starts from Toss's own figures and moves them by
the live price only; the briefing's account uses it; the won-based split uses Toss's own rate at each execution."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.integration.test_service_api import NOW
from tests.integration.test_toss_broker import connect, world  # noqa: F401
from tests.toss_fake import holding, order


def rows_of(acct: dict) -> dict[str, dict]:
    return {r["ticker"]: r for r in acct["rows"]}


def test_received_yesterday_trade_never_marks_account_live(world):
    c, svc, fake, _keys = world
    assert connect(c, fake).status_code == 200
    svc.quotes.ingest_poll("NVDA", 900, NOW - timedelta(days=1), "toss")
    account = c.get("/api/portfolio/live").json()
    assert not account["live"]
    assert rows_of(account)["NVDA"]["price"] == 120.5


def test_missing_broker_amounts_remain_unknown_and_partial(world, monkeypatch):
    c, svc, fake, _keys = world
    assert connect(c, fake).status_code == 200
    snapshot = svc.broker.snapshot()
    for h in snapshot["holdings"]:
        if h["symbol"] == "NVDA":
            h.update(market_value=None, pnl=None, purchase_amount=None)
    monkeypatch.setattr(svc.broker, "snapshot", lambda: snapshot)
    account = c.get("/api/portfolio/live").json()
    assert rows_of(account)["NVDA"]["value"] is None
    assert rows_of(account)["NVDA"]["pnl"] is None
    assert account["totals"]["pnl_count"] == 1
    assert account["totals"]["daily_rate"] is None
    assert any("부분 합계" in note for note in account["notes"])


def test_the_account_is_tosss_own_figures_moved_by_the_live_price(world):  # noqa: F811
    c, svc, fake, _keys = world
    assert connect(c, fake).status_code == 200
    a = c.get("/api/portfolio/live").json()
    nv = rows_of(a)["NVDA"]
    # no live price yet: exactly Toss's numbers (10 × $100 bought, valued at $120.50, today +1 % of the value)
    assert nv["basis"] == "TOSS" and nv["price"] == 120.5 and nv["live"] is False
    assert nv["purchase"] == pytest.approx(1000) and nv["value"] == pytest.approx(1205) and nv["pnl"] == pytest.approx(205)
    assert nv["daily"] == pytest.approx(12.05) and nv["pnl_rate"] == pytest.approx(0.205)

    svc._clock["t"] = NOW + timedelta(seconds=5)
    svc.quotes.ingest_poll("NVDA", 125.5, NOW + timedelta(seconds=4), "toss")  # a newer price than the sync's
    a = c.get("/api/portfolio/live").json()
    nv, brk = rows_of(a)["NVDA"], rows_of(a)["BRK.B"]
    assert nv["live"] is True and nv["price"] == 125.5
    assert nv["value"] == pytest.approx(1255) and nv["pnl"] == pytest.approx(255) and nv["daily"] == pytest.approx(62.05)  # each + 10 × $5
    assert brk["price"] == 410 and brk["daily"] == pytest.approx(10.25)  # no live price: Toss's own figures
    t = a["totals"]
    assert t["pnl"] == pytest.approx(255 + 25) and t["daily"] == pytest.approx(62.05 + 10.25)
    assert t["pnl_rate"] == pytest.approx(280 / 2000)
    # in won the Toss way: the dollar figures at the current 매매기준율 (1,380)
    assert a["fx"]["rate"] == 1380 and nv["pnl_krw"] == round(255 * 1380) and t["daily_krw"] == round((62.05 + 10.25) * 1380)
    # the domestic holding (삼성전자) stays out of the US account figures
    assert "005930" not in rows_of(a)


def test_a_price_older_than_the_sync_moves_nothing(world):  # noqa: F811
    c, svc, fake, _keys = world
    svc.quotes.ingest_poll("NVDA", 90.0, NOW - timedelta(seconds=30), "toss")  # printed before Toss valued the account
    assert connect(c, fake).status_code == 200
    nv = rows_of(c.get("/api/portfolio/live").json())["NVDA"]
    assert nv["price"] == 120.5 and nv["pnl"] == pytest.approx(205) and nv["live"] is False


def test_the_briefing_shows_the_accounts_today(world):  # noqa: F811
    c, svc, fake, _keys = world
    assert connect(c, fake).status_code == 200
    svc._clock["t"] = NOW + timedelta(seconds=5)
    svc.quotes.ingest_poll("NVDA", 125.5, NOW + timedelta(seconds=4), "toss")
    acct = c.get("/api/briefing?refresh=true").json()["account"]
    # Toss's today (a share bought today counts from its purchase price) + 10 × $5 — not Σ (price − previous close)
    assert acct["basis"] == "TOSS" and acct["pnl"] == pytest.approx(62.05 + 10.25) and acct["priced"] == 2
    assert acct["total_pnl"] == pytest.approx(280) and acct["pnl_krw"] == round((62.05 + 10.25) * 1380)


def test_the_won_split_uses_tosss_rate_at_each_execution(world):  # noqa: F811
    c, svc, fake, _keys = world
    fake.items = [holding("NVDA", "10", "100.00", "120.50")]
    fake.orders = [order(1, "NVDA", "BUY", "10", "100.00", "2026-09-02")]
    asked: list[str] = []

    def rate_at(when: str) -> dict[str, str]:
        asked.append(when)
        return {"rate": "1300.00", "midRate": "1295.00"}
    fake.rate_at = rate_at
    assert connect(c, fake).status_code == 200
    k = c.get("/api/portfolio").json()["krw"]
    assert next(r for r in k["rows"] if r["ticker"] == "NVDA")["basis"] == "REFERENCE"  # connecting stays quick: FRED until the next sync
    assert c.post("/api/broker/toss/sync").status_code == 200
    assert asked and datetime.fromisoformat(asked[0]) == datetime.fromisoformat("2026-09-02T23:31:00+09:00")  # the execution's own moment
    k = c.get("/api/portfolio").json()["krw"]
    nv = next(r for r in k["rows"] if r["ticker"] == "NVDA")
    assert nv["known"] and nv["basis"] == "BROKER" and nv["buy_fx"] == pytest.approx(1300)
    assert "토스증권 매수 환율" in k["note"]
    c.post("/api/broker/toss/sync")
    assert len(asked) == 1  # once per execution, kept
