"""오늘의 브리핑 (owner 2026-09-29: first "아침 브리핑 07:00", then "고정되어 있으면 도움이 안 돼 — 오늘의 브리핑, 실시간"):
what it says, that it follows the live prices, and that a part without data says so instead of disappearing.
(The 07:00 readiness and once-a-day alert tests of the first version were removed with those features.)"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.integration.test_service_api import NOW
from tests.integration.test_transactions import client  # noqa: F401

MORNING = NOW + timedelta(hours=8)  # Fri 2026-09-25 23:00 UTC = Sat 08:00 KST, after Friday's close


def test_the_briefing_sums_up_the_session_for_my_account(client):  # noqa: F811
    c, svc = client
    c.put("/api/portfolio", json={"holdings": [{"ticker": "NVDA", "quantity": 10, "cost_basis": 100}, {"ticker": "MSFT", "quantity": 2, "cost_basis": 300}]})
    assert c.post("/api/scan").status_code == 200  # the LIVE harness found the candidate list reading fields rows do not have
    svc._clock["t"] = MORNING
    b = c.get("/api/briefing").json()
    assert b["scan_as_of"] is not None
    for cand in b["candidates"]:
        assert cand["action_ko"] and cand["score"] > 0 and (cand["max_buy"] is None or cand["max_buy"] > 0)
    assert b["date_kst"] == "2026-09-26" and b["session_expected"] == "2026-09-25"
    # the mock world's daily bars end on the 24th (as when the night's bars are not stored yet): said, not blank
    assert b["session"] == "2026-09-24" and any("9/25 종가가 아직" in n for n in b["notes"])
    assert [m["ticker"] for m in b["market"]] == ["SPY", "QQQ"] and all(m["change"] is not None for m in b["market"])
    acct = b["account"]
    # the account's today is the 9/25 session's: its closes are not stored and no live price came — not calculated,
    # said so (owner 2026-09-29: "내 계좌 수익이 안 맞아" — the 9/24 move was shown as today's before)
    assert acct["holdings"] == 2 and acct["priced"] == 0 and acct["pnl"] is None and acct["movers"] == []
    assert any("오늘 손익에서 뺐습니다" in n for n in acct["notes"])
    assert "S&P 500" in b["headline"] and "내 계좌" not in b["headline"]
    assert isinstance(b["events"], list) and isinstance(b["candidates"], list) and isinstance(b["watch"], list)


def _close(svc, ticker, day):
    bars = svc.store.bars(ticker, day, day) if getattr(svc, "store", None) is not None else svc.data.bars(ticker, day - timedelta(days=7), day).value
    return next(b.close for b in bars if b.day == day)


def test_the_accounts_today_is_the_live_price_against_the_previous_close(client):  # noqa: F811
    c, svc = client
    c.put("/api/portfolio", json={"holdings": [{"ticker": "NVDA", "quantity": 10, "cost_basis": 100}, {"ticker": "MSFT", "quantity": 2, "cost_basis": 300}]})
    now = svc.now()  # Fri 9/25 11:00 New York, in the session
    prev = {t: _close(svc, t, now.date() - timedelta(days=1)) for t in ("NVDA", "MSFT")}
    svc.quotes.ingest_poll("NVDA", prev["NVDA"] * 1.03, now - timedelta(seconds=1), "toss")
    b = c.get("/api/briefing?refresh=true").json()
    acct = b["account"]
    # MSFT has no live price in the session: left out of today's figure (not its previous move), and said
    assert acct["priced"] == 1 and acct["pnl"] == pytest.approx(10 * prev["NVDA"] * 0.03, abs=0.02)
    assert [m["ticker"] for m in acct["movers"]] == ["NVDA"] and any("1종목" in n for n in acct["notes"])
    assert "내 계좌" in b["headline"]


def test_the_briefing_follows_the_live_prices(client):  # noqa: F811
    c, svc = client
    c.put("/api/portfolio", json={"holdings": [{"ticker": "NVDA", "quantity": 10, "cost_basis": 100}]})
    svc._clock["t"] = NOW  # in the session (Fri 11:00 New York): the live price against Thursday's close
    before = c.get("/api/briefing?refresh=true").json()
    assert before["live"] is False  # no live print yet: the stored closes, said so
    spy_prev = before["market"][0]["close"]
    now = svc.now()
    svc.quotes.ingest_poll("SPY", spy_prev * 1.02, now - timedelta(seconds=1), "toss")  # the live feed: SPY +2 %
    svc.quotes.ingest_poll("NVDA", 500.0, now - timedelta(seconds=1), "toss")
    b = c.get("/api/briefing?refresh=true").json()
    spy = b["market"][0]
    assert b["live"] is True and spy["live"] is True and spy["close"] == spy_prev * 1.02
    assert abs(spy["change"] - 0.02) < 0.02  # against the last close before the live session
    nv = next(m for m in b["account"]["movers"] if m["ticker"] == "NVDA")
    assert nv["close"] == 500.0 and b["live_at"]


def test_an_empty_account_says_so(client):  # noqa: F811
    c, svc = client
    c.put("/api/portfolio", json={"holdings": []})
    svc._clock["t"] = MORNING
    b = c.get("/api/briefing").json()
    assert b["account"]["holdings"] == 0 and b["account"]["pnl"] is None and b["account"]["movers"] == []
    assert "내 계좌" not in b["headline"]
